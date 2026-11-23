"""Turns a checked debrief into updates on the single deal record: terms, change log, actions, emails, alerts."""
import datetime as dt
import json
import os

import ai
import db
import guards

SPEAKER = os.environ.get("BB_BROKER_NAME", "Alex Moreno")
TITLE = os.environ.get("BB_BROKER_TITLE", "Senior Vice President, Capital Markets")
LEAD_DAYS = int(os.environ.get("BB_ALERT_LEAD_DAYS", "3"))

LABELS = {
    "price": "Price", "stated_cap_rate": "Stated cap rate", "deposit": "Earnest money deposit", "dd_days": "Due diligence",
    "dd_start_date": "PSA execution / DD start", "dd_end_date": "DD ends / deposit goes hard", "closing_days_after_dd": "Closing timing",
    "closing_date": "Closing date", "financing": "Financing", "dd_extension_days": "DD extension", "seller_price_expectation": "Seller's price expectation",
    "internal_estimate": "Internal estimate (confidential)", "psa_draft_date": "PSA draft due", "calculated_cap_rate": "Cap rate on T-12 NOI",
}
ORDER = list(LABELS)
MONEY_FIELDS = {"price", "deposit", "internal_estimate"}
DATE_FIELDS = {"dd_start_date", "dd_end_date", "closing_date", "psa_draft_date"}


def today():
    return dt.date.fromisoformat(os.environ["BB_TODAY"]) if os.environ.get("BB_TODAY") else dt.date.today()


def fmt_date(s):
    if not s:
        return ""
    d = dt.date.fromisoformat(s) if isinstance(s, str) else s
    return f"{d:%b} {d.day}, {d.year}"


def fmt(field, t):
    if t is None:
        return ""
    if t["status"] == "not_mentioned":
        return "Not mentioned"
    if field in DATE_FIELDS and t.get("value_date"):
        return fmt_date(t["value_date"])
    n = t.get("value_number")
    if n is not None:
        if field in MONEY_FIELDS:
            return f"${n:,.0f}"
        if field in ("stated_cap_rate", "calculated_cap_rate"):
            return f"{n * 100:.2f}%"
        if field in ("dd_days", "dd_extension_days"):
            return f"{n:.0f} days"
        if field == "closing_days_after_dd":
            return f"{n:.0f} days after DD ends"
    return t.get("value_text") or ""


def current_terms(deal_id):
    return {t["field"]: t for t in db.rows("SELECT * FROM terms WHERE deal_id=?", deal_id)}


def _set(con, deal_id, field, status, number=None, text=None, date=None, evidence="", debrief_id=None, log=True):
    old = con.execute("SELECT * FROM terms WHERE deal_id=? AND field=?", (deal_id, field)).fetchone()
    new = {"status": status, "value_number": number, "value_text": text, "value_date": date}
    if old and status == "not_mentioned" and old["status"] != "not_mentioned":
        return  # a follow-up that doesn't mention a term keeps the earlier value
    con.execute("INSERT OR REPLACE INTO terms VALUES (?,?,?,?,?,?,?,?)", (deal_id, field, text, number, date, status, evidence, debrief_id))
    if log and old and old["status"] not in ("not_mentioned",) and status != "not_mentioned":
        o, n = fmt(field, dict(old)), fmt(field, new)
        if o != n:
            con.execute("INSERT INTO change_log (deal_id, field, old, new, debrief_id, at) VALUES (?,?,?,?,?,?)",
                        (deal_id, field, o, n, debrief_id, dt.datetime.now().isoformat(timespec="seconds")))


def _recalculate(con, deal_id, debrief_id, extension=None):
    t = {r["field"]: dict(r) for r in con.execute("SELECT * FROM terms WHERE deal_id=?", (deal_id,)).fetchall()}
    ok = lambda f: f in t and t[f]["status"] not in ("not_mentioned", "needs_review")
    dd_end = None
    if extension and ok("dd_end_date"):
        dd_end = dt.date.fromisoformat(t["dd_end_date"]["value_date"]) + dt.timedelta(days=extension)
        ev = f"Calculated: previous DD end + {extension:.0f}-day extension"
    elif ok("dd_start_date") and ok("dd_days"):
        dd_end = dt.date.fromisoformat(t["dd_start_date"]["value_date"]) + dt.timedelta(days=t["dd_days"]["value_number"])
        ev = "Calculated: DD start + DD days"
    if dd_end:
        _set(con, deal_id, "dd_end_date", "calculated", date=dd_end.isoformat(), evidence=ev, debrief_id=debrief_id)
        if ok("closing_days_after_dd"):
            close = dd_end + dt.timedelta(days=t["closing_days_after_dd"]["value_number"])
            _set(con, deal_id, "closing_date", "calculated", date=close.isoformat(), evidence="Calculated: DD end + closing days", debrief_id=debrief_id)
    noi = con.execute("SELECT noi FROM deals WHERE id=?", (deal_id,)).fetchone()["noi"]
    price = con.execute("SELECT * FROM terms WHERE deal_id=? AND field='price'", (deal_id,)).fetchone()
    if price and price["value_number"] and price["status"] == "agreed":
        _set(con, deal_id, "calculated_cap_rate", "calculated", number=round(noi / price["value_number"], 6),
             evidence="Calculated: T-12 NOI / agreed price", debrief_id=debrief_id)


def facts_for_email(deal_id):
    """Only agreed or calculated facts. Never confidential, never superseded, never unverified."""
    t = current_terms(deal_id)
    keep = ["price", "deposit", "dd_days", "dd_start_date", "dd_end_date", "closing_date", "financing"]
    out = {}
    for f in keep:
        if f in t and t[f]["status"] in ("agreed", "calculated", "stated"):
            out[LABELS[f]] = fmt(f, t[f])
    return out


def allowed_money(deal_id):
    t = current_terms(deal_id)
    return [t[f]["value_number"] for f in ("price", "deposit") if f in t and t[f]["status"] == "agreed" and t[f]["value_number"]]


def forbidden_money(deal_id, superseded):
    out = [(s["number"], "it was " + ("corrected in the debrief" if s["reason"] == "correction" else "the other side's rejected proposal"))
           for s in superseded if s.get("number") and s["number"] > 1000]
    for t in current_terms(deal_id).values():
        if t["status"] == "internal_confidential" and t["value_number"]:
            out.append((t["value_number"], "it is a confidential internal estimate"))
    for c in db.rows("SELECT old, new FROM change_log WHERE deal_id=? AND field IN ('price','deposit')", deal_id):
        old = guards.money_values(c["old"])
        if old:
            out.append((old[0], f"it is outdated: the deal record now says {c['new']}"))
    for c in db.rows("SELECT text FROM suggestions WHERE deal_id=? AND kind='superseded'", deal_id):
        n, why = json.loads(c["text"])
        out.append((n, why))
    return out


DRAFTING = {}


def process(transcript, date, speed="balanced", background=False):
    deals = db.rows("SELECT * FROM deals")
    contacts = db.rows("SELECT * FROM contacts")
    terms_now = {d["id"]: {f: fmt(f, t) for f, t in current_terms(d["id"]).items()} for d in deals}
    result, source, secs = ai.extract(transcript, date, deals, contacts, terms_now, speed)

    deal_id = result.get("deal_id")
    if deal_id not in {d["id"] for d in deals}:
        raise ValueError("Could not match this debrief to a deal. Say the property name or address.")
    ev_fail = guards.evidence_failures(result, transcript)
    bad_terms = {f for kind, f, _ in ev_fail if kind == "term"}
    invented = guards.invented_names(result, transcript, contacts)

    con = db.connect()
    cur = con.execute("INSERT INTO debriefs (deal_id, date, speaker, transcript, processed_at, seconds, source, flags) VALUES (?,?,?,?,?,?,?,?)",
                      (deal_id, date, SPEAKER, transcript, dt.datetime.now().isoformat(timespec="seconds"), secs, source, "{}"))
    bid = cur.lastrowid

    extension = None
    for t in result.get("terms", []):
        f = t["field"]
        status = "needs_review" if f in bad_terms else t["status"]
        if f == "dd_extension_days" and status == "agreed":
            extension = t.get("number")
        _set(con, deal_id, f, status, t.get("number"), t.get("text"), t.get("date"), t.get("evidence", ""), bid)
    con.commit()
    _recalculate(con, deal_id, bid, extension)

    for s in result.get("superseded", []):
        why = "it was " + ("corrected in the debrief" if s["reason"] == "correction" else "the other side's rejected proposal")
        con.execute("INSERT INTO suggestions (deal_id, debrief_id, kind, text, status) VALUES (?,?,?,?,?)",
                    (deal_id, bid, "superseded", json.dumps([s["number"], why]), "info"))

    for a in result.get("actions", []):
        c, label = guards.resolve_person(a.get("owner_name"), contacts, SPEAKER)
        side = a["owner_type"]
        if side == "unassigned" or not label:
            label, side = "Unassigned", "unassigned"
        elif c:
            side = "internal" if c["side"] == "Internal" else "external"
        con.execute("INSERT INTO actions (deal_id, debrief_id, description, owner_id, owner_label, side, due_date, due_text, evidence) VALUES (?,?,?,?,?,?,?,?,?)",
                    (deal_id, bid, a["description"], c["id"] if c else None, label, side, a.get("due_date"), a.get("due_text"), a.get("evidence")))
        if side == "unassigned":
            con.execute("INSERT INTO suggestions (deal_id, debrief_id, kind, text) VALUES (?,?,?,?)",
                        (deal_id, bid, "assign", f"No owner was named for \"{a['description']}\". Assign it on the Team page."))

    for n in result.get("new_contacts", []):
        con.execute("INSERT INTO suggestions (deal_id, debrief_id, kind, text) VALUES (?,?,?,?)",
                    (deal_id, bid, "new_contact", f"{n['name']} ({n['relationship']}) is not in the CRM. Add a full name and email before contacting them."))
    terms_after = {r["field"]: r for r in con.execute("SELECT * FROM terms WHERE deal_id=?", (deal_id,)).fetchall()}
    if "price" in terms_after and terms_after["price"]["status"] == "agreed":
        for f, msg in (("closing_date", "Closing timing was not mentioned. Confirm it before the PSA."),
                       ("stated_cap_rate", None)):
            if msg and f in terms_after and terms_after[f]["status"] == "not_mentioned":
                con.execute("INSERT INTO suggestions (deal_id, debrief_id, kind, text) VALUES (?,?,?,?)", (deal_id, bid, "missing", msg))
    con.commit()
    con.close()

    deal = db.one("SELECT * FROM deals WHERE id=?", deal_id)
    superseded = result.get("superseded", [])
    plan = plan_recipients(result, deal, contacts, bid, deal_id)
    flags = {"evidence_failures": ev_fail, "invented_names": invented,
             "superseded": [{"field": LABELS.get(s["field"], s["field"]), "value": f"${s['number']:,.0f}" if s["number"] > 1000 else f"{s['number']:.0f}",
                             "reason": s["reason"], "evidence": s["evidence"]} for s in superseded],
             "drafts_planned": len(plan), "meeting_type": result.get("meeting_type"), "counterparty": " · ".join(x for x in [result.get("counterparty_name"), result.get("counterparty_company")] if x)}
    db.execute("UPDATE debriefs SET flags=? WHERE id=?", json.dumps(flags), bid)
    if background:  # deal notes are ready now; drafts finish a few seconds later
        import threading
        th = threading.Thread(target=_draft_all, args=(plan, deal, deal_id, bid, superseded), daemon=True)
        DRAFTING[bid] = th
        th.start()
    else:
        _draft_all(plan, deal, deal_id, bid, superseded)
    return bid


def drafting(bid):
    th = DRAFTING.get(bid)
    return bool(th and th.is_alive())


def wait_for_drafts(bid, timeout=120):
    th = DRAFTING.get(bid)
    if th:
        th.join(timeout)


def _safe_draft(pack):
    try:
        return ai.draft_email(pack, "turbo")
    except Exception:  # an API hiccup must not lose the email: fall back to the checked template
        return (*ai.template_email(pack), "template")


def _draft_all(plan, deal, deal_id, bid, superseded):
    facts = facts_for_email(deal_id)
    packs = [{"to_name": c["name"], "to_company": c["company"], "audience": p["audience"], "purpose": p["purpose"],
              "deal_name": deal["name"], "address": deal["address"], "points": p["points"], "facts": facts} for c, p in plan]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:  # write all drafts at the same time
        drafts = list(pool.map(_safe_draft, packs))
    for (c, p), (subject, body, _src) in zip(plan, drafts):
        body, wire = guards.add_wire_warning(body)
        body = body.rstrip() + f"\n\nBest regards,\n{SPEAKER}\n{TITLE}"
        issues = guards.email_issues(subject, body, allowed_money(deal_id), forbidden_money(deal_id, superseded), bool(c["email"]))
        db.execute("INSERT INTO emails (deal_id, debrief_id, to_id, to_name, to_email, audience, purpose, subject, body, status, issues, wire, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   deal_id, bid, c["id"], c["name"], c["email"], p["audience"], p["purpose"], subject, body, "Draft",
                   json.dumps(issues), int(wire), dt.datetime.now().isoformat(timespec="seconds"))




def _words(t):
    stop = {"the", "a", "an", "to", "and", "of", "for", "by", "please", "on", "with", "s", "is", "be", "in", "your", "our"}
    return {w for w in guards.norm(t).replace("'", " ").split() if w not in stop and len(w) > 2}


def _merge_points(model_points, required):
    """Keep the model's wording; add a required task line only if no model line already covers it."""
    out = list(model_points)
    for r in required:
        rw = _words(r)
        covered = any(len(rw & _words(m)) >= max(2, len(rw) // 2) for m in model_points)
        if not covered:
            out.insert(0, r)
    return out


def plan_recipients(result, deal, contacts, bid, deal_id):
    """Code decides who gets an email; the model only suggests wording.
    1) every outside party given a task, 2) the counterparty, 3) anyone the broker says he will contact ("I'll let Marco know").
    Nobody else: a lender who was only mentioned gets nothing."""
    by_id = {}
    def want(name, audience, purpose, points):
        c, _ = guards.resolve_person(name, contacts, SPEAKER)
        if not c or c["side"] == "Internal":
            return
        if not c.get("email"):
            return
        if c["id"] not in by_id:
            by_id[c["id"]] = (c, {"audience": audience, "purpose": purpose, "points": list(points)})
        else:
            by_id[c["id"]][1]["points"] += [p for p in points if p not in by_id[c["id"]][1]["points"]]
    suggested = {}
    for e in result.get("emails_needed", []):
        c, _ = guards.resolve_person(e["to_name"], contacts, SPEAKER)
        if c:
            suggested[c["id"]] = e
    def use(name, audience, purpose, points, required=False):
        c, _ = guards.resolve_person(name, contacts, SPEAKER)
        if c and c["id"] in suggested:
            e = suggested[c["id"]]
            # A task the broker assigned is always in the email, whatever wording the model suggested.
            merged = _merge_points(e.get("points") or [], points) if required else (e.get("points") or points)
            want(name, e.get("audience", audience), e.get("purpose", purpose), merged)
        else:
            want(name, audience, purpose, points)
    for a in result.get("actions", []):
        if a.get("owner_type") == "external" and a.get("owner_name"):
            c, _ = guards.resolve_person(a["owner_name"], contacts, SPEAKER)
            if c and not c.get("email"):
                continue
            due = f" by {fmt_date(a['due_date'])}" if a.get("due_date") else ""
            use(a["owner_name"], "vendor", a["description"][:60], [f"Please {a['description'][0].lower()}{a['description'][1:]}{due}"], required=True)
    cp = result.get("counterparty_name")
    cpc, _ = guards.resolve_person(cp, contacts, SPEAKER) if cp else (None, None)
    if cpc and cpc["name"] == deal["seller_contact"]:
        # The broker negotiated with our own client; the follow-up belongs to the other side already on record.
        for b in db.rows("SELECT flags FROM debriefs WHERE deal_id=? AND id<? ORDER BY id DESC", deal_id, bid):
            prev = (json.loads(b["flags"] or "{}").get("counterparty") or "").split(" · ")[0]
            pc, _ = guards.resolve_person(prev, contacts, SPEAKER) if prev else (None, None)
            if pc and pc["name"] != deal["seller_contact"]:
                cp = pc["name"]
                break
    if cp:
        c, _ = guards.resolve_person(cp, contacts, SPEAKER)
        is_client = bool(c) and (c["name"] == deal["seller_contact"] or "seller" in (c["role"] or "").lower())
        use(cp, "client" if is_client else "counterparty", "Follow-up", ["Thank you for the conversation", "Here is where things stand"])
    for a in result.get("actions", []):
        if (a.get("owner_name") or "").strip().lower() in guards.SPEAKER_WORDS:
            words = set(guards.norm(a["description"]).split())
            for c in contacts:
                if c["side"] != "Internal" and c["name"].split()[0].lower() in words:
                    use(c["name"], "vendor", a["description"][:60], [f"Following up: {a['description'][0].lower()}{a['description'][1:]}"])
    return list(by_id.values())


def recheck_email(email_id):
    e = db.one("SELECT * FROM emails WHERE id=?", email_id)
    issues = guards.email_issues(e["subject"], e["body"], allowed_money(e["deal_id"]), forbidden_money(e["deal_id"], []), bool(e["to_email"]))
    db.execute("UPDATE emails SET issues=? WHERE id=?", json.dumps(issues), email_id)
    return issues


def alerts(deal_id=None):
    """'One step ahead': every dated event, with the day the broker should be warned."""
    out = []
    deals = {d["id"]: d for d in db.rows("SELECT * FROM deals")}
    for d_id, d in deals.items():
        if deal_id and d_id != deal_id:
            continue
        t = current_terms(d_id)
        dep = fmt("deposit", t.get("deposit")) if t.get("deposit", {}).get("status") == "agreed" else ""
        named = {"psa_draft_date": "PSA draft due", "dd_start_date": "PSA execution and DD start",
                 "dd_end_date": f"Deposit goes hard{' (' + dep + ')' if dep else ''}", "closing_date": "Closing"}
        for f, label in named.items():
            if f in t and t[f].get("value_date") and t[f]["status"] != "not_mentioned":
                out.append({"deal_id": d_id, "deal": d["name"], "label": label, "date": t[f]["value_date"], "kind": "deal"})
        for a in db.rows("SELECT * FROM actions WHERE deal_id=? AND done=0 AND due_date IS NOT NULL", d_id):
            out.append({"deal_id": d_id, "deal": d["name"], "label": f"{a['description']} ({a['owner_label']})", "date": a["due_date"], "kind": "task"})
        for tn in db.rows("SELECT * FROM tenants WHERE deal_id=? AND expiration IS NOT NULL", d_id):
            exp = dt.date.fromisoformat(tn["expiration"][:10])
            if 0 <= (exp - today()).days <= 120:
                out.append({"deal_id": d_id, "deal": d["name"], "label": f"{tn['tenant']} lease expires ({tn['sf']:,.0f} SF)", "date": exp.isoformat(), "kind": "lease"})
    for a in out:
        ev = dt.date.fromisoformat(a["date"])
        a["alert_date"] = (ev - dt.timedelta(days=LEAD_DAYS)).isoformat()
        a["days"] = (ev - today()).days
        a["when"] = "Overdue" if a["days"] < 0 else ("Today" if a["days"] == 0 else ("Tomorrow" if a["days"] == 1 else f"In {a['days']} days"))
        a["urgent"] = a["days"] <= LEAD_DAYS
    return sorted(out, key=lambda a: a["date"])
