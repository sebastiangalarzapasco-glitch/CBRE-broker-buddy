"""Runs Broker Buddy against the answer key (Broker_Buddy_Answer_Key.xlsx / data/golden_set.json).

Part 1 checks the whole pipeline on the four sample debriefs (Sample mode answers).
Part 2 feeds the checks deliberately wrong model answers to prove they are caught.
Run: python -m pytest -q
"""
import copy
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ["BB_DB_PATH"] = str(tmp_path_factory.mktemp("db") / "t.db")
    os.environ["BB_TODAY"] = "2026-11-20"
    import db, engine
    importlib.reload(db); importlib.reload(engine)
    db.init(reset=True)
    g = db.golden()
    ids = {}
    for d in g["debriefs"]:
        ids[d["Debrief ID"]] = engine.process(d["Transcript (what the broker says)"], d["Date"])
    return db, engine, g, ids


def terms_after(db, engine, deal):
    return {f: engine.fmt(f, t) for f, t in engine.current_terms(deal).items()}


# ---------------- Part 1: the answer key ----------------

def test_final_deal_terms_match_key(run):
    db, engine, g, _ = run
    d1, d2, d3 = (terms_after(db, engine, x) for x in ("D1", "D2", "D3"))
    assert d1["price"] == "$29,300,000"            # agreed, not the buyer's $29.22M proposal
    assert d1["deposit"] == "$750,000"             # corrected from $500K
    assert d1["dd_start_date"] == "Nov 6, 2026"
    assert d1["dd_end_date"] == "Dec 31, 2026"     # Dec 21 + 10-day extension
    assert d1["closing_date"] == "Jan 30, 2027"
    assert d2["price"] == "$18,250,000"            # corrected from $18.5M
    assert d2["deposit"] == "$400,000" and d2["dd_days"] == "30 days"
    assert d2["closing_date"] == "Not mentioned" and d2["stated_cap_rate"] == "Not mentioned"
    assert "Riverbend" in d2["financing"] and "6.40%" in d2["financing"]
    assert d2["psa_draft_date"] == "Nov 11, 2026"
    assert d3["price"] == "Not mentioned"
    assert d3["internal_estimate"] == "$40,500,000"
    for f in ("deposit", "dd_days", "closing_date", "financing"):
        assert d3[f] == "Not mentioned"


def test_cap_rates_match_t12(run):
    db, engine, g, _ = run
    t = engine.current_terms("D1")
    assert round(t["calculated_cap_rate"]["value_number"] * 100, 2) == 5.57
    assert round(engine.current_terms("D2")["calculated_cap_rate"]["value_number"] * 100, 2) == 6.79


def test_change_log_on_retrade(run):
    db, engine, g, ids = run
    log = {(r["field"], r["old"], r["new"]) for r in db.rows("SELECT * FROM change_log WHERE debrief_id=?", ids["B4"])}
    assert ("price", "$29,700,000", "$29,300,000") in log
    assert ("dd_end_date", "Dec 21, 2026", "Dec 31, 2026") in log
    assert ("closing_date", "Jan 20, 2027", "Jan 30, 2027") in log


def test_action_owners_and_dates(run):
    db, engine, g, ids = run
    got = db.rows("SELECT a.*, b.id bid FROM actions a JOIN debriefs b ON b.id=a.debrief_id")
    key = g["expected_actions"]
    assert len(got) == len(key) == 16
    inv = {v: k for k, v in ids.items()}
    matched = 0
    for k in key:
        rows = [a for a in got if inv[a["bid"]] == k["Debrief"]]
        owner = k["Owner"].split(" (")[0]
        hit = [a for a in rows if (a["owner_label"] == owner) or (owner.startswith("Unassigned") and a["owner_label"] == "Unassigned")
               or (owner == "Daniel" and a["owner_label"] == "Daniel")]
        due = k["Due date"]
        if hit and any((a["due_date"] or None) == (due or None) for a in hit):
            matched += 1
    assert matched == 16


def test_unassigned_task_is_flagged_not_guessed(run):
    db, *_ = run
    a = db.one("SELECT * FROM actions WHERE description LIKE '%renewal history%'")
    assert a["owner_label"] == "Unassigned" and a["owner_id"] is None
    assert db.one("SELECT * FROM suggestions WHERE kind='assign'")


def test_exact_email_recipients(run):
    db, engine, g, ids = run
    inv = {v: k for k, v in ids.items()}
    got = sorted((inv[e["debrief_id"]], e["to_name"]) for e in db.rows("SELECT * FROM emails"))
    want = sorted((e["Debrief"], e["To"].split(",")[0]) for e in g["expected_emails"])
    assert got == want
    names = [e["to_name"] for e in db.rows("SELECT * FROM emails")]
    assert "Dana Whitfield" not in names and not any(n.startswith("Daniel") for n in names)


def test_emails_pass_must_not_rules(run):
    db, *_ = run
    for e in db.rows("SELECT * FROM emails"):
        text = e["subject"] + e["body"]
        for bad in ("500,000", "18,500,000", "29,220,000", "40,500,000"):
            assert bad not in text, (e["to_name"], bad)
        assert json.loads(e["issues"]) == []


def test_wire_warning_only_on_escrow_email(run):
    db, *_ = run
    wired = [(e["debrief_id"], e["to_name"]) for e in db.rows("SELECT * FROM emails WHERE wire=1")]
    assert wired == [(1, "Marco Delgado")]
    body = db.one("SELECT body FROM emails WHERE wire=1")["body"]
    assert "verify any wiring instructions by phone" in body


def test_new_contact_suggested_not_invented(run):
    db, *_ = run
    s = db.one("SELECT * FROM suggestions WHERE kind='new_contact'")
    assert s and s["text"].startswith("Daniel (")


def test_missing_closing_flagged(run):
    db, *_ = run
    assert db.one("SELECT * FROM suggestions WHERE kind='missing' AND deal_id='D2'")


def test_alerts_one_step_ahead(run):
    db, engine, *_ = run
    a = {(x["deal_id"], x["label"].split(" (")[0], x["date"]) for x in engine.alerts()}
    assert ("D1", "Deposit goes hard", "2026-12-31") in a
    assert ("D1", "Closing", "2027-01-30") in a
    assert ("D2", "Summit Ridge Engineering lease expires", "2026-12-31") in a
    assert not any(x[2] == "2026-12-21" for x in a)   # old deadline replaced


def test_review_before_send(run):
    import app as A
    c = A.app.test_client()
    e = run[0].one("SELECT * FROM emails WHERE status='Draft' AND deal_id='D2'")
    c.post(f"/emails/{e['id']}/approve", data={"reviewer": ""})
    assert run[0].one("SELECT status FROM emails WHERE id=?", e["id"])["status"] == "Draft"
    c.post(f"/emails/{e['id']}/approve", data={"reviewer": "Alex Moreno"})
    s = run[0].one("SELECT status, approved_by FROM emails WHERE id=?", e["id"])
    assert s["status"] == "Approved, not sent" and s["approved_by"] == "Alex Moreno"


def test_stale_draft_blocked_after_retrade(run):
    import app as A
    db = run[0]
    e = db.one("SELECT * FROM emails WHERE deal_id='D1' AND debrief_id=1 AND to_name='Owen Pratt'")
    issues = A.engine.recheck_email(e["id"])
    assert any("outdated" in i["text"] and "29,300,000" in i["text"] for i in issues)


def test_editing_in_a_bad_number_blocks_approval(run):
    import app as A
    db = run[0]
    c = A.app.test_client()
    e = db.one("SELECT * FROM emails WHERE status='Draft' AND deal_id='D3'")
    c.post(f"/emails/{e['id']}/save", data={"subject": e["subject"], "body": e["body"] + "\nOur view is closer to $40.5M."})
    issues = json.loads(db.one("SELECT issues FROM emails WHERE id=?", e["id"])["issues"])
    assert any("confidential" in i["text"] for i in issues)
    c.post(f"/emails/{e['id']}/approve", data={"reviewer": "Alex Moreno"})
    assert db.one("SELECT status FROM emails WHERE id=?", e["id"])["status"] == "Draft"


def test_share_link_hides_internal_estimate(run):
    import app as A
    db = run[0]
    tok = db.one("SELECT share_token FROM deals WHERE id='D3'")["share_token"]
    html = A.app.test_client().get(f"/share/{tok}").data.decode()
    assert "Trinity Commons" in html and "40,500,000" not in html


def test_every_page_loads(run):
    import app as A
    c = A.app.test_client()
    for p in ["/", "/deals/D1", "/deals/D2", "/deals/D3", "/emails", "/team", "/how-it-works", "/debrief", "/debriefs/1", "/debriefs/4"]:
        assert c.get(p).status_code == 200, p


# ---------------- Part 2: wrong model answers are caught ----------------

def _fixture(b):
    from fixtures import FIXTURES
    return copy.deepcopy(FIXTURES[b]["result"]), FIXTURES[b]["transcript"]


def test_guard_rejects_quote_not_in_transcript():
    import guards
    r, t = _fixture("B1")
    r["terms"][0]["evidence"] = "They're in at thirty million"
    assert ("term", "price", "They're in at thirty million") in guards.evidence_failures(r, t)


def test_guard_catches_invented_names():
    import guards, db
    r, t = _fixture("B3")
    r["new_contacts"][0]["name"] = "Daniel Whitley"
    r["emails_needed"].append({"to_name": "Sarah Kim", "purpose": "x", "audience": "client", "points": []})
    bad = guards.invented_names(r, t, db.rows("SELECT * FROM contacts"))
    assert "Sarah Kim" in bad and "Daniel Whitley" in bad


def test_guard_blocks_superseded_and_unknown_numbers():
    import guards
    issues = guards.email_issues("Terms", "Deposit is $500,000 and price is $29.7M, fee $12,345.",
                                 allowed=[29700000, 750000], forbidden=[(500000, "it was corrected")])
    texts = " ".join(i["text"] for i in issues)
    assert "$500,000 must not appear" in texts and "$12,345 is not in the deal record" in texts
    assert "$29,700,000" not in texts


def test_guard_requires_wire_warning():
    import guards
    assert any("fraud warning" in i["text"] for i in guards.email_issues("x", "Please send wiring instructions.", [], []))
    body, wire = guards.add_wire_warning("Please send wiring instructions.")
    assert wire and guards.email_issues("x", body, [], []) == []


def test_corrected_value_cannot_reach_record():
    """If the model took the first number instead of the correction, the quote check still flags it."""
    import guards
    r, t = _fixture("B1")
    dep = next(x for x in r["terms"] if x["field"] == "deposit")
    dep["number"], dep["evidence"] = 500000, "Earnest money is five hundred thousand"
    assert any(f == "deposit" for k, f, _ in guards.evidence_failures(r, t))


def test_spoken_weekdays_match_dates():
    import datetime as dt
    for day, name in (("2026-11-04", "Wednesday"), ("2026-11-06", "Friday"), ("2026-11-11", "Wednesday"),
                      ("2026-11-13", "Friday"), ("2026-11-09", "Monday"), ("2026-11-23", "Monday")):
        assert dt.date.fromisoformat(day).strftime("%A") == name


def test_code_picks_recipients_even_if_model_suggests_none(tmp_path, monkeypatch):
    """Live Claude once drafted only 3 of 9 emails. Recipients are now decided by code."""
    import copy as _c, db, engine, ai, fixtures
    monkeypatch.setenv("BB_DB_PATH", str(tmp_path / "r.db"))
    importlib.reload(db); importlib.reload(engine)
    db.init(reset=True)
    real = {k: v for k, v in fixtures.FIXTURES.items()}
    def fake(transcript, *a, **k):
        for fx in real.values():
            if fx["transcript"] == transcript:
                r = _c.deepcopy(fx["result"]); r["emails_needed"] = []
                return r, "sample", 0.0
    monkeypatch.setattr(ai, "extract", fake)
    g = db.golden(); ids = {}
    for d in g["debriefs"]:
        ids[d["Debrief ID"]] = engine.process(d["Transcript (what the broker says)"], d["Date"])
    inv = {v: k for k, v in ids.items()}
    got = sorted((inv[e["debrief_id"]], e["to_name"]) for e in db.rows("SELECT * FROM emails"))
    want = sorted((e["Debrief"], e["To"].split(",")[0]) for e in g["expected_emails"])
    assert got == want
    assert [e["to_name"] for e in db.rows("SELECT * FROM emails WHERE wire=1")] == ["Marco Delgado"]


def test_followup_goes_to_buyer_not_our_client(tmp_path, monkeypatch):
    """Live Claude named Carla (our seller client) as counterparty on the retrade. The email must still go to the buyer."""
    import copy as _c, db, engine, ai, fixtures
    monkeypatch.setenv("BB_DB_PATH", str(tmp_path / "c.db"))
    importlib.reload(db); importlib.reload(engine)
    db.init(reset=True)
    real = dict(fixtures.FIXTURES)
    def fake(transcript, *a, **k):
        for key, fx in real.items():
            if fx["transcript"] == transcript:
                r = _c.deepcopy(fx["result"])
                if key == "B4":
                    r["counterparty_name"], r["counterparty_company"], r["emails_needed"] = "Carla Benavides", "Northpoint Holdings LP", []
                return r, "sample", 0.0
    monkeypatch.setattr(ai, "extract", fake)
    g = db.golden()
    for d in g["debriefs"]:
        engine.process(d["Transcript (what the broker says)"], d["Date"])
    b4 = sorted(e["to_name"] for e in db.rows("SELECT * FROM emails WHERE debrief_id=4"))
    assert b4 == ["Marco Delgado", "Owen Pratt", "Priya Raman"]


def test_background_drafting_returns_before_emails(tmp_path, monkeypatch):
    import db, engine, ai, time as _t
    monkeypatch.setenv("BB_DB_PATH", str(tmp_path / "bg.db"))
    importlib.reload(db); importlib.reload(engine)
    db.init(reset=True)
    slow = ai.template_email
    monkeypatch.setattr(ai, "draft_email", lambda pack, speed="balanced": (_t.sleep(0.5), (*slow(pack), "template"))[1])
    d = db.golden()["debriefs"][0]
    t0 = _t.time()
    bid = engine.process(d["Transcript (what the broker says)"], d["Date"], background=True)
    assert _t.time() - t0 < 0.4 and engine.drafting(bid)
    engine.wait_for_drafts(bid)
    assert len(db.rows("SELECT * FROM emails WHERE debrief_id=?", bid)) == 3


# ---------------- One step ahead: meetings, reminders and prep briefs ----------------

def test_prep_brief_built_from_record(tmp_path, monkeypatch):
    import db, engine, meetings
    monkeypatch.setenv("BB_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("BB_NOW", "2026-11-04T13:40")
    importlib.reload(db); importlib.reload(engine); importlib.reload(meetings)
    db.init(reset=True); meetings.ensure_table()
    g = db.golden()
    for d in g["debriefs"][:3]:
        engine.process(d["Transcript (what the broker says)"], d["Date"])
    # reminder: Priya's 2:00 PM call is 20 minutes away
    r = meetings.reminder()
    assert r and r["contact"] == "Priya Raman" and r["when"] == "In 20 min"
    b = meetings.brief(r["id"])
    snap = {s["label"]: s["value"] for s in b["snapshot"]}
    assert snap["Price"] == "$29,700,000" and snap["PSA execution / DD start"] == "Nov 6, 2026"
    text = " ".join(b["points"])
    assert "still a draft" in text                      # the recap email to Priya is unapproved
    assert "Send Priya the rent roll" in text           # Jenna's task for Priya was due Nov 2
    assert b["last"] is not None
    # Graham's brief carries the confidential warning and the missing-contact prompt; nothing invented
    g_id = next(m["id"] for m in meetings.upcoming() if m["contact"] == "Graham Whitley")
    bg = meetings.brief(g_id)
    assert bg["confidential"] == ["$40,500,000"]
    assert any("Daniel" in p for p in bg["points"])


def test_meeting_pages_and_banner(tmp_path, monkeypatch):
    import db, engine, meetings
    monkeypatch.setenv("BB_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setenv("BB_NOW", "2026-11-04T13:45")
    importlib.reload(db); importlib.reload(engine); importlib.reload(meetings)
    import app as A
    importlib.reload(A)
    c = A.app.test_client()
    html = c.get("/").data.decode()
    assert "Open prep brief" in html and "Priya Raman" in html
    assert c.get("/meetings").status_code == 200
    assert c.get("/meetings/1").status_code == 200
    r = c.get("/meetings/1/debrief")
    assert r.status_code == 302 and "Priya" in r.headers["Location"]
    r = c.post("/meetings/add", data={"deal_id": "D2", "contact_id": "C08", "title": "Call", "start": "2026-11-04T15:00", "location": ""})
    assert r.status_code == 302 and c.get(r.headers["Location"]).status_code == 200
