"""The Intelligence layer: Claude reads a debrief and returns structured facts with evidence.

Claude never writes to the database directly. Its answer is checked by guards.py first.
Without an ANTHROPIC_API_KEY the app runs in Sample mode: the four golden debriefs return
recorded answers, clearly labelled as sample results.
"""
import datetime as dt
import json
import os
import re
import time

FIELDS = [
    "price", "stated_cap_rate", "deposit", "dd_days", "dd_start_date", "closing_days_after_dd", "closing_date",
    "financing", "dd_extension_days", "seller_price_expectation", "internal_estimate", "psa_draft_date",
]
STATUSES = ["agreed", "stated", "expectation", "internal_confidential", "not_mentioned"]

TOOL = {
    "name": "record_debrief",
    "description": "Record the facts from a broker's spoken meeting debrief. Every value must be backed by a verbatim quote.",
    "input_schema": {
        "type": "object",
        "properties": {
            "deal_id": {"type": "string", "description": "ID of the deal discussed, from the deal list"},
            "deal_evidence": {"type": "string"},
            "meeting_type": {"type": "string", "description": "Short label, e.g. 'Buyer tour and terms', 'Listing pitch', 'Retrade call'"},
            "counterparty_name": {"type": ["string", "null"]},
            "counterparty_company": {"type": ["string", "null"]},
            "terms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": FIELDS},
                        "status": {"type": "string", "enum": STATUSES},
                        "number": {"type": ["number", "null"], "description": "Dollars as a plain number; rates as a fraction (5.5% = 0.055); days as a number"},
                        "text": {"type": ["string", "null"]},
                        "date": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD resolved from the debrief date"},
                        "evidence": {"type": "string", "description": "Exact words copied from the transcript. Empty only if not_mentioned."},
                    },
                    "required": ["field", "status", "evidence"],
                },
            },
            "superseded": {
                "type": "array",
                "description": "Numbers that were said but must NOT be used: self-corrections and the other side's rejected proposals",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": FIELDS},
                        "number": {"type": "number"},
                        "reason": {"type": "string", "enum": ["correction", "rejected_proposal"]},
                        "evidence": {"type": "string"},
                    },
                    "required": ["field", "number", "reason", "evidence"],
                },
            },
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "owner_name": {"type": ["string", "null"], "description": "As said. 'I' means the speaker. Null if nobody was named."},
                        "owner_type": {"type": "string", "enum": ["internal", "external", "unassigned"]},
                        "due_date": {"type": ["string", "null"], "description": "ISO date, or null if no date was said"},
                        "due_text": {"type": "string", "description": "Exactly how the timing was said, or 'Not stated'"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["description", "owner_type", "due_text", "evidence"],
                },
            },
            "emails_needed": {
                "type": "array",
                "description": "Outside parties who should get an email because of this meeting. Never a person the broker did not ask to contact, and never someone with no email on file.",
                "items": {
                    "type": "object",
                    "properties": {
                        "to_name": {"type": "string"},
                        "purpose": {"type": "string"},
                        "audience": {"type": "string", "enum": ["client", "counterparty", "vendor"]},
                        "points": {"type": "array", "items": {"type": "string"}, "description": "What the email must say, in plain words"},
                    },
                    "required": ["to_name", "purpose", "audience", "points"],
                },
            },
            "new_contacts": {
                "type": "array",
                "items": {"type": "object", "properties": {"name": {"type": "string"}, "relationship": {"type": "string"}, "evidence": {"type": "string"}},
                          "required": ["name", "relationship", "evidence"]},
            },
        },
        "required": ["deal_id", "deal_evidence", "meeting_type", "terms", "superseded", "actions", "emails_needed", "new_contacts"],
    },
}

SYSTEM = """You turn a commercial real estate broker's spoken debrief into structured facts for the deal record.
Rules, in order of importance:
1. Use only what the transcript says plus the deal list and contacts provided. Never invent a name, company, property name, number or date.
2. Copy evidence word for word from the transcript.
3. If the broker corrects himself ("five hundred, no, sorry, seven fifty"), use the corrected value and put the discarded one in superseded.
4. If the other side proposed something and a different value was agreed, record the agreed value and put the proposal in superseded as rejected_proposal.
5. If a term was not discussed, record it with status not_mentioned. Do not guess.
6. The broker's own internal views on value are internal_confidential and must never be shared with clients.
7. Resolve relative dates ("this Friday", "Wednesday the fourth", "today") from the debrief date given.
8. For a follow-up on an existing deal, record only what changed or was restated.
Record the deal by calling record_debrief."""


def _call_tool(client, model, tool, messages, max_tokens, system=None):
    """Ask for a structured answer through a tool. Some models reject a forced tool_choice,
    so fall back to 'auto' and remind the model to call the tool."""
    import anthropic
    kw = {"model": model, "max_tokens": max_tokens, "tools": [tool], "messages": messages}
    if system:
        kw["system"] = system
    try:
        msg = client.messages.create(tool_choice={"type": "tool", "name": tool["name"]}, **kw)
    except anthropic.BadRequestError as e:
        if "tool_choice" not in str(e):
            raise
        kw["messages"] = messages[:-1] + [{"role": "user", "content": messages[-1]["content"] + f"\n\nAnswer only by calling the {tool['name']} tool."}]
        msg = client.messages.create(tool_choice={"type": "auto"}, **kw)
    for b in msg.content:
        if b.type == "tool_use":
            return b.input
    raise RuntimeError("The model did not return a structured answer. Try again.")


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key)


def model_for(speed):
    if speed == "turbo":
        return os.environ.get("BB_MODEL_TURBO", "claude-haiku-5-5")
    return os.environ.get("BB_MODEL", "claude-sonnet-5-5")


def live_mode():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def extract(transcript, debrief_date, deals, contacts, current_terms, speed="balanced"):
    """Returns (result_dict, source, seconds). source is 'claude:<model>' or 'sample'."""
    t0 = time.time()
    client = _client()
    if client is None:
        from fixtures import FIXTURES
        for fx in FIXTURES.values():
            if _norm(fx["transcript"]) == _norm(transcript):
                return json.loads(json.dumps(fx["result"])), "sample", round(time.time() - t0, 2)
        raise RuntimeError("Sample mode only knows the four sample debriefs. Add an ANTHROPIC_API_KEY to process new ones.")
    d = dt.date.fromisoformat(debrief_date)
    context = {
        "debrief_date": f"{d.isoformat()} ({d.strftime('%A')})",
        "deals": [{"id": x["id"], "name": x["name"], "address": x["address"], "asset_type": x["asset_type"]} for x in deals],
        "contacts": [{"name": c["name"], "company": c["company"], "role": c["role"], "side": c["side"]} for c in contacts],
        "current_terms_by_deal": current_terms,
    }
    model = model_for(speed)
    result = _call_tool(client, model, TOOL, [{"role": "user", "content": f"Context:\n{json.dumps(context, indent=1)}\n\nTranscript:\n{transcript}"}],
                        4000, system=SYSTEM)
    return result, f"claude:{model}", round(time.time() - t0, 2)


EMAIL_TOOL = {
    "name": "write_email",
    "description": "Write one email",
    "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]},
}


def draft_email(pack, speed="balanced"):
    """pack holds only the facts this recipient may see. Returns (subject, body, source)."""
    client = _client()
    if client is None:
        s, b = template_email(pack)
        return s, b, "template"
    prompt = (
        "Write a short, professional email from a commercial real estate broker. Use ONLY the facts below; "
        "do not add numbers, names or dates that are not listed. Cover every point listed. Never mention wiring, wire transfers, "
        "bank details or account numbers unless a point explicitly asks for them. Plain language, no filler, under 140 words. "
        "Do not include a signature; it is added automatically.\n\n" + json.dumps(pack, indent=1)
    )
    out = _call_tool(client, model_for(speed), EMAIL_TOOL, [{"role": "user", "content": prompt}], 800)
    return out["subject"], out["body"], "claude"


def template_email(pack):
    first = pack["to_name"].split()[0]
    subject = f"{pack['deal_name']}: {pack['purpose']}"
    lines = [f"Hi {first},", ""]
    opener = {
        "client": f"Thanks for your time on {pack['deal_name']}. A quick recap so we are aligned:",
        "counterparty": f"Following up on {pack['deal_name']}. Here is where things stand:",
        "vendor": f"Writing about {pack['deal_name']} ({pack['address']}). Here is what we need:",
    }[pack["audience"]]
    points = list(pack["points"])
    if pack["audience"] == "client" and points and points[0].lower().startswith("thank"):
        opener = points.pop(0).rstrip(".") + ". A quick recap so we are aligned:"
    lines.append(opener)
    lines.append("")
    for p in points:
        lines.append(f"- {p}")
    if pack.get("facts"):
        lines.append("")
        lines.append("Key terms:")
        for k, v in pack["facts"].items():
            lines.append(f"- {k}: {v}")
    lines += ["", "Please let me know if anything here does not match your notes."]
    return subject, "\n".join(lines)
