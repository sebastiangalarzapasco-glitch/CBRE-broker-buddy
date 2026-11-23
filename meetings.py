"""One step ahead: upcoming meetings, reminders and a prep brief built only from the deal record.

No AI is used here. Every line of a brief comes from stored terms, tasks, emails, alerts and the change log,
so a brief cannot invent anything.
"""
import datetime as dt
import os

import db
import engine

REMIND_MIN = int(os.environ.get("BB_REMIND_MINUTES", "30"))

SAMPLE_MEETINGS = [
    ("D1", "C04", "PSA status call", "2026-11-04T14:00", "Phone"),
    ("D1", "C05", "Seller update", "2026-11-04T16:30", "Northpoint Holdings office"),
    ("D2", "C08", "Second tour", "2026-11-10T10:00", "1800 Cedar Row Dr, Dallas"),
    ("D3", "C11", "Unit walk", "2026-11-12T09:30", "Trinity Commons leasing office"),
]


def ensure_table():
    con = db.connect()
    con.execute("""CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT, contact_id TEXT, title TEXT, start TEXT, location TEXT)""")
    if con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0:
        con.executemany("INSERT INTO meetings (deal_id, contact_id, title, start, location) VALUES (?,?,?,?,?)", SAMPLE_MEETINGS)
    con.commit()
    con.close()


def now():
    if os.environ.get("BB_NOW"):
        return dt.datetime.fromisoformat(os.environ["BB_NOW"])
    clock = dt.datetime.now()
    return dt.datetime.combine(engine.today(), clock.time().replace(microsecond=0))


def when_text(start):
    n = now()
    mins = int((start - n).total_seconds() // 60)
    if -60 <= mins < 0:
        return "Now"
    if 0 <= mins < 60:
        return f"In {mins} min"
    day = (start.date() - n.date()).days
    t = start.strftime("%I:%M %p").lstrip("0")
    if day == 0:
        return f"Today {t}"
    if day == 1:
        return f"Tomorrow {t}"
    return f"{start:%a}, {start:%b} {start.day} · {t}"


def _row(m):
    m = dict(m)
    m["start_dt"] = dt.datetime.fromisoformat(m["start"])
    m["when"] = when_text(m["start_dt"])
    m["minutes"] = int((m["start_dt"] - now()).total_seconds() // 60)
    m["prep_ready"] = m["minutes"] <= REMIND_MIN
    return m


def upcoming(limit=None, include_past_minutes=60):
    rows = db.rows("""SELECT m.*, c.name contact, c.role, c.company, d.name deal FROM meetings m
                      JOIN contacts c ON c.id=m.contact_id JOIN deals d ON d.id=m.deal_id ORDER BY m.start""")
    out = [_row(r) for r in rows]
    out = [m for m in out if m["minutes"] >= -include_past_minutes]
    return out[:limit] if limit else out


def reminder():
    """The meeting to warn about on every page: starts within the reminder window or is under way."""
    for m in upcoming():
        if -60 <= m["minutes"] <= REMIND_MIN:
            return m
    return None


def add(deal_id, contact_id, title, start, location):
    return db.execute("INSERT INTO meetings (deal_id, contact_id, title, start, location) VALUES (?,?,?,?,?)",
                      deal_id, contact_id, title, start, location)


def brief(meeting_id):
    m = db.one("""SELECT m.*, c.name contact, c.role, c.company, c.email, c.phone, c.side, d.name deal, d.address, d.seller_contact
                  FROM meetings m JOIN contacts c ON c.id=m.contact_id JOIN deals d ON d.id=m.deal_id WHERE m.id=?""", meeting_id)
    if not m:
        return None
    m = _row(m)
    deal_id, first = m["deal_id"], m["contact"].split()[0]
    terms = engine.current_terms(deal_id)
    show = ["price", "deposit", "dd_days", "dd_start_date", "dd_end_date", "closing_date", "financing", "psa_draft_date",
            "seller_price_expectation", "calculated_cap_rate"]
    snapshot = [{"label": engine.LABELS[f], "value": engine.fmt(f, terms[f]), "status": terms[f]["status"]} for f in show if f in terms]
    confidential = [engine.fmt(f, t) for f, t in terms.items() if t["status"] == "internal_confidential"]

    debriefs = db.rows("SELECT * FROM debriefs WHERE deal_id=? ORDER BY id", deal_id)
    touched = [b for b in debriefs if first.lower() in (b["transcript"] or "").lower()]
    last = touched[-1] if touched else None
    since_id = last["id"] if last else 0
    changes = db.rows("SELECT c.*, b.date FROM change_log c LEFT JOIN debriefs b ON b.id=c.debrief_id WHERE c.deal_id=? AND c.debrief_id>? ORDER BY c.id",
                      deal_id, since_id) if last else []

    acts = db.rows("SELECT * FROM actions WHERE deal_id=? AND done=0 ORDER BY due_date IS NULL, due_date", deal_id)
    theirs = [a for a in acts if a["owner_id"] == m["contact_id"]]
    about_them = [a for a in acts if a["owner_id"] != m["contact_id"] and first.lower() in a["description"].lower()]
    team = [a for a in acts if a["side"] in ("internal", "unassigned") and a not in about_them]
    emails = db.rows("SELECT * FROM emails WHERE deal_id=? AND to_id=? ORDER BY id DESC", deal_id, m["contact_id"])
    dates = [a for a in engine.alerts(deal_id) if 0 <= a["days"] <= 30 and a["kind"] != "task"]

    points = []
    if not debriefs:
        points.append("First meeting on this deal: nothing is on record yet. Record a debrief right after.")
    price_agreed = "price" in terms and terms["price"]["status"] == "agreed"
    if price_agreed:
        for f in ("closing_date", "financing", "deposit", "dd_days"):
            if f in terms and terms[f]["status"] == "not_mentioned":
                points.append(f"Confirm {engine.LABELS[f].lower()}: it has not been discussed yet.")
    for a in dates:
        if a["label"].startswith("Deposit goes hard"):
            points.append(f"{a['label']} on {engine.fmt_date(a['date'])}: check where diligence stands.")
        elif a["kind"] == "lease":
            points.append(f"{a['label']} on {engine.fmt_date(a['date'])}: be ready with renewal status.")
    today = engine.today().isoformat()
    for a in theirs:
        late = a["due_date"] and a["due_date"] < today
        points.append(f"Ask about: {a['description'].lower()}" + (f" (was due {engine.fmt_date(a['due_date'])})" if late else ""))
    for a in about_them:
        if a["due_date"] and a["due_date"] < today:
            points.append(f"Check it was done before the meeting: {a['description']} ({a['owner_label']}, due {engine.fmt_date(a['due_date'])}).")
    for e in emails:
        if e["status"] == "Draft":
            points.append(f"Your email \"{e['subject']}\" to {first} is still a draft. Review it before you meet.")
    for c in changes:
        points.append(f"Since you last spoke, {engine.LABELS.get(c['field'], c['field']).lower()} changed from {c['old']} to {c['new']}.")
    for s in db.rows("SELECT * FROM suggestions WHERE deal_id=? AND kind='new_contact' AND status='open'", deal_id):
        name = s["text"].split(" (")[0]
        points.append(f"Ask for {name}'s full name and email so they can be added to the CRM.")
    return {"m": m, "snapshot": snapshot, "confidential": confidential, "last": last, "changes": changes,
            "theirs": theirs, "about_them": about_them, "team": team, "emails": emails, "dates": dates, "points": points}
