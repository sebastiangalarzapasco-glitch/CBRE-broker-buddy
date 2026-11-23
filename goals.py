"""Goals: work backwards from an income goal to the weekly activity it takes.

Income goal -> deals to close -> listings to win -> BOVs / pitches -> meetings -> contacts.
All math is here, in code, and every step rounds up (you cannot close half a deal).
The default assumptions are examples only, clearly labelled in the app, until the broker
enters their own or CBRE shares real fee, split and win-rate figures.

Actual pace comes from what Broker Buddy already records, with no extra typing:
every debrief is a logged meeting and every BOV is a logged pitch.
"""
import datetime as dt
import math

import db
import engine

FIELDS = [
    # key, label, default, kind, (min, max)
    ("income_goal", "Income goal for the year", 500_000, "money", (1_000, 100_000_000)),
    ("avg_deal", "Average deal size", 25_000_000, "money", (100_000, 5_000_000_000)),
    ("fee_pct", "Fee on the deal", 1.0, "pct", (0.01, 10)),
    ("share_pct", "Your share of the fee", 40.0, "pct", (1, 100)),
    ("close_pct", "Listings that close", 70.0, "pct", (1, 100)),
    ("win_pct", "Pitches / BOVs you win", 30.0, "pct", (1, 100)),
    ("pitch_pct", "Meetings that lead to a pitch", 25.0, "pct", (1, 100)),
    ("meet_pct", "Outreach that becomes a meeting", 10.0, "pct", (1, 100)),
    ("weeks", "Working weeks in the year", 48, "int", (1, 52)),
]
DEFAULTS = {k: d for k, _, d, _, _ in FIELDS}


def ensure_table():
    con = db.connect()
    con.execute("CREATE TABLE IF NOT EXISTS goals (key TEXT PRIMARY KEY, value REAL)")
    con.commit()
    con.close()


def load():
    ensure_table()
    saved = {r["key"]: r["value"] for r in db.rows("SELECT * FROM goals")}
    return {k: saved.get(k, d) for k, d in DEFAULTS.items()}, bool(saved)


def save(values):
    ensure_table()
    for k, v in values.items():
        db.execute("INSERT OR REPLACE INTO goals VALUES (?,?)", k, v)


def reset():
    ensure_table()
    db.execute("DELETE FROM goals")


def parse(form):
    """Validate the form. Returns (values, errors)."""
    out, errors = {}, []
    for k, label, _, kind, (lo, hi) in FIELDS:
        raw = (form.get(k) or "").replace(",", "").replace("$", "").replace("%", "").strip()
        try:
            v = float(raw)
        except ValueError:
            errors.append(f"{label}: enter a number.")
            continue
        if not lo <= v <= hi:
            errors.append(f"{label}: must be between {lo:,g} and {hi:,g}.")
            continue
        out[k] = int(v) if kind == "int" else v
    return out, errors


def funnel(v):
    """Each step rounds up."""
    per_deal = v["avg_deal"] * v["fee_pct"] / 100 * v["share_pct"] / 100
    deals = math.ceil(v["income_goal"] / per_deal - 1e-9)
    listings = math.ceil(deals / (v["close_pct"] / 100) - 1e-9)
    pitches = math.ceil(listings / (v["win_pct"] / 100) - 1e-9)
    meetings = math.ceil(pitches / (v["pitch_pct"] / 100) - 1e-9)
    contacts = math.ceil(meetings / (v["meet_pct"] / 100) - 1e-9)
    w = v["weeks"]
    steps = [
        {"key": "deals", "label": "Deals to close", "n": deals, "how": f"${v['income_goal']:,.0f} ÷ ${per_deal:,.0f} per deal"},
        {"key": "listings", "label": "Listings to win", "n": listings, "how": f"{v['close_pct']:g}% of listings close"},
        {"key": "pitches", "label": "BOVs / pitches", "n": pitches, "how": f"you win {v['win_pct']:g}% of pitches"},
        {"key": "meetings", "label": "Client meetings", "n": meetings, "how": f"{v['pitch_pct']:g}% of meetings lead to a pitch"},
        {"key": "contacts", "label": "People to contact", "n": contacts, "how": f"{v['meet_pct']:g}% of outreach becomes a meeting"},
    ]
    for s in steps:
        s["per_week"] = s["n"] / w
    return {"per_deal": per_deal, "steps": steps, "by_key": {s["key"]: s for s in steps}}


def actual_pace(days=28):
    """Recorded activity over the last 4 weeks, per week. Comes from debriefs and BOVs; no typing."""
    today = engine.today()
    start = (today - dt.timedelta(days=days)).isoformat()
    meetings = db.one("SELECT COUNT(*) n FROM debriefs WHERE date > ? AND date <= ?", start, today.isoformat())["n"]
    try:
        cutoff = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
        pitches = db.one("SELECT COUNT(DISTINCT deal_id) n FROM bovs WHERE created_at > ?", cutoff)["n"]
    except Exception:
        pitches = 0
    weeks = days / 7
    return {"weeks": weeks, "meetings": meetings, "pitches": pitches,
            "meetings_pw": meetings / weeks, "pitches_pw": pitches / weeks}


def compare(need_pw, have_pw):
    if need_pw <= 0:
        return "ok", 1.0
    ratio = have_pw / need_pw
    return ("ok" if ratio >= 1 else "close" if ratio >= 0.7 else "behind"), ratio
