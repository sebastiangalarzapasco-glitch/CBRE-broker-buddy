"""Scores the LIVE model (Claude) against the answer key. Needs ANTHROPIC_API_KEY.

Usage:  python eval_golden.py            (most careful model)
        python eval_golden.py turbo      (fastest model)
Uses a separate throwaway database, so your demo data is untouched.
"""
import os, sys, tempfile, time

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("Set ANTHROPIC_API_KEY first.")
os.environ["BB_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "eval.db")
os.environ["BB_TODAY"] = "2026-11-20"
speed = sys.argv[1] if len(sys.argv) > 1 else "balanced"
import db, engine  # noqa: E402

db.init(reset=True)
g = db.golden()
ids, times = {}, []
for d in g["debriefs"]:
    t0 = time.time()
    bid = engine.process(d["Transcript (what the broker says)"], d["Date"], speed, background=True)
    times.append(time.time() - t0)          # what the broker waits for: deal notes on screen
    engine.wait_for_drafts(bid)
    ids[d["Debrief ID"]] = bid
    print(f"{d['Debrief ID']}: deal notes in {times[-1]:.1f} s, drafts done at {time.time() - t0:.1f} s")

T = lambda deal: {f: engine.fmt(f, t) for f, t in engine.current_terms(deal).items()}
d1, d2, d3 = T("D1"), T("D2"), T("D3")
inv = {v: k for k, v in ids.items()}
emails = db.rows("SELECT * FROM emails")
got_to = sorted((inv[e["debrief_id"]], e["to_name"]) for e in emails)
want_to = sorted((e["Debrief"], e["To"].split(",")[0]) for e in g["expected_emails"])
alltext = " ".join(e["subject"] + e["body"] for e in emails)
log = {(r["field"], r["old"], r["new"]) for r in db.rows("SELECT * FROM change_log WHERE debrief_id=?", ids["B4"])}
checks = [
    ("Deal terms match", d1.get("price") == "$29,300,000" and d1.get("deposit") == "$750,000" and d1.get("dd_end_date") == "Dec 31, 2026"
     and d1.get("closing_date") == "Jan 30, 2027" and d2.get("price") == "$18,250,000" and d2.get("deposit") == "$400,000" and d2.get("dd_days") == "30 days"),
    ("Corrections honoured", d1.get("deposit") == "$750,000" and d2.get("price") == "$18,250,000"),
    ("Agreed beats proposed", d1.get("price") == "$29,300,000" and d1.get("dd_end_date") == "Dec 31, 2026"),
    ("'Not mentioned' instead of guessing", d2.get("closing_date") in (None, "Not mentioned") and d3.get("price") in (None, "Not mentioned")
     and all(d3.get(f) in (None, "Not mentioned") for f in ("deposit", "dd_days", "financing"))),
    ("No invented names", all(not __import__("json").loads(b["flags"]).get("invented_names") for b in db.rows("SELECT flags FROM debriefs"))),
    ("Right recipients", got_to == want_to),
    ("Must not include", not any(x in alltext for x in ("500,000", "18,500,000", "29,220,000", "40,500,000", "$40.5"))),
    ("Wire-fraud warning", [(inv[e["debrief_id"]], e["to_name"]) for e in emails if e["wire"]] == [("B1", "Marco Delgado")]),
    ("Change log on update", {("price", "$29,700,000", "$29,300,000"), ("dd_end_date", "Dec 21, 2026", "Dec 31, 2026")} <= log),
    ("Speed (deal notes under 10 s)", max(times) <= 10),
]
print("\nCheck".ljust(42), "Result")
for name, ok in checks:
    print(name.ljust(41), "PASS" if ok else "FAIL")
print(f"\n{sum(ok for _, ok in checks)} of {len(checks)} passed. Average {sum(times)/len(times):.1f} s per debrief.")
wired = [(inv[e["debrief_id"]], e["to_name"]) for e in emails if e["wire"]]
if wired != [("B1", "Marco Delgado")]:
    print("Wire warning on:", wired, " expected: [('B1', 'Marco Delgado')]")
if got_to != want_to:
    print("Recipients got:", got_to, "\nexpected:", want_to)
