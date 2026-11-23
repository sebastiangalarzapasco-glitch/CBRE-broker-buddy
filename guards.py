"""Checks that run on every model answer and every email before a broker sees it.

Flawless communication rule: the model proposes, these checks verify, the broker approves.
"""
import re

WIRE = re.compile(r"\bwir(e|es|ing)\b|\bwire transfer\b|\brouting number\b", re.I)
WIRE_LINE = ("For your security, please verify any wiring instructions by phone using a number you already "
             "know before sending funds. We will never change wiring instructions by email.")
SPEAKER_WORDS = {"i", "me", "myself", "speaker"}


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", (s or "").lower().replace("’", "'"))).strip()


def quote_found(evidence, transcript):
    e = norm(evidence)
    return bool(e) and e in norm(transcript)


def evidence_failures(result, transcript):
    """Every value the model claims must be backed by words that are really in the transcript."""
    bad = []
    for t in result.get("terms", []):
        if t.get("status") != "not_mentioned" and not quote_found(t.get("evidence"), transcript):
            bad.append(("term", t["field"], t.get("evidence")))
    for a in result.get("actions", []):
        if not quote_found(a.get("evidence"), transcript):
            bad.append(("action", a.get("description"), a.get("evidence")))
    for s in result.get("superseded", []):
        if not quote_found(s.get("evidence"), transcript):
            bad.append(("superseded", s.get("field"), s.get("evidence")))
    if not quote_found(result.get("deal_evidence"), transcript):
        bad.append(("deal", result.get("deal_id"), result.get("deal_evidence")))
    return bad


def resolve_person(name, contacts, speaker):
    """Map a spoken name to a CRM contact. Returns (contact or None, label)."""
    if not name:
        return None, None
    n = name.strip()
    if n.lower() in SPEAKER_WORDS:
        c = next((c for c in contacts if c["name"] == speaker), None)
        return c, speaker
    exact = [c for c in contacts if c["name"].lower() == n.lower()]
    if exact:
        return exact[0], exact[0]["name"]
    first = [c for c in contacts if c["name"].split()[0].lower() == n.split()[0].lower()]
    if len(first) == 1:
        return first[0], first[0]["name"]
    return None, n


def invented_names(result, transcript, contacts):
    """Names the model used that appear neither in the transcript nor in the CRM."""
    t = norm(transcript)
    crm = {norm(c["name"]) for c in contacts} | {norm(c["company"]) for c in contacts}
    names = [result.get("counterparty_name"), result.get("counterparty_company")]
    names += [a.get("owner_name") for a in result.get("actions", [])]
    names += [e.get("to_name") for e in result.get("emails_needed", [])]
    names += [c.get("name") for c in result.get("new_contacts", [])]
    out = []
    for n in names:
        if not n or n.lower() in SPEAKER_WORDS:
            continue
        nn = norm(n)
        if nn in crm or nn in t:
            continue
        if any(norm(c["name"]).startswith(nn.split()[0]) for c in contacts) and nn.split()[0] in t:
            continue
        out.append(n)
    return sorted(set(out))


MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(million|mm|m|k|thousand)?\b", re.I)


def money_values(text):
    vals = []
    for m in MONEY.finditer(text or ""):
        v = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        if unit in ("million", "mm", "m"):
            v *= 1_000_000
        elif unit in ("k", "thousand"):
            v *= 1_000
        vals.append(v)
    return vals


def _near(a, b):
    return abs(a - b) <= max(1.0, 0.002 * abs(b))


def email_issues(subject, body, allowed, forbidden, has_address=True):
    """Returns a list of {level, text}. Any 'block' issue stops the email from being approved."""
    issues = []
    text = f"{subject}\n{body}"
    for v in money_values(text):
        if any(_near(v, f) for f, _ in forbidden):
            why = next(w for f, w in forbidden if _near(v, f))
            issues.append({"level": "block", "text": f"${v:,.0f} must not appear: {why}"})
        elif not any(_near(v, a) for a in allowed):
            issues.append({"level": "block", "text": f"${v:,.0f} is not in the deal record. Check it or remove it."})
    if WIRE.search(text) and WIRE_LINE not in body:
        issues.append({"level": "block", "text": "Mentions wiring but the fraud warning is missing."})
    if not has_address:
        issues.append({"level": "block", "text": "No email address on file for this person."})
    return issues


def add_wire_warning(body):
    if WIRE.search(body) and WIRE_LINE not in body:
        return body.rstrip() + "\n\n" + WIRE_LINE, True
    return body, bool(WIRE.search(body))
