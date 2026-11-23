"""Goals: the reverse funnel is checked against the worked example.

$500,000 goal, $25M average deal, 1.0% fee, 40% share -> $100,000 per deal -> 5 deals.
5 / 70% = 7.1 -> 8 listings; 8 / 30% = 26.7 -> 27 pitches; 27 / 25% = 108 meetings; 108 / 10% = 1,080 contacts.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def goals(tmp_path_factory):
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ["BB_DB_PATH"] = str(tmp_path_factory.mktemp("goals") / "t.db")
    os.environ["BB_TODAY"] = "2026-11-20"
    import db, engine, goals
    importlib.reload(db); importlib.reload(engine); importlib.reload(goals)
    db.init(reset=True)
    return goals


def test_worked_example(goals):
    f = goals.funnel(goals.DEFAULTS)
    assert f["per_deal"] == pytest.approx(100_000)
    assert [s["n"] for s in f["steps"]] == [5, 8, 27, 108, 1080]
    assert f["by_key"]["contacts"]["per_week"] == pytest.approx(22.5)
    assert f["by_key"]["meetings"]["per_week"] == pytest.approx(2.25)


def test_exact_division_does_not_round_up_twice(goals):
    v = dict(goals.DEFAULTS, income_goal=400_000, close_pct=100, win_pct=50, pitch_pct=50, meet_pct=50)
    assert [s["n"] for s in goals.funnel(v)["steps"]] == [4, 4, 8, 16, 32]


def test_parse_validates(goals):
    form = {k: str(d) for k, d in goals.DEFAULTS.items()}
    v, errs = goals.parse(dict(form, income_goal="$750,000", fee_pct="1.25%"))
    assert not errs and v["income_goal"] == 750_000 and v["fee_pct"] == 1.25
    _, errs = goals.parse(dict(form, win_pct="0"))
    assert errs and "between" in errs[0]
    _, errs = goals.parse(dict(form, weeks="abc"))
    assert errs and "number" in errs[0]


def test_save_load_reset(goals):
    v, saved = goals.load()
    assert not saved and v == goals.DEFAULTS
    goals.save(dict(goals.DEFAULTS, income_goal=1_000_000))
    v, saved = goals.load()
    assert saved and v["income_goal"] == 1_000_000
    goals.reset()
    assert goals.load()[1] is False


def test_actual_pace_counts_debriefs(goals):
    import db
    for d in ("2026-11-02", "2026-11-03", "2026-11-04", "2026-11-20", "2026-09-01"):
        db.execute("INSERT INTO debriefs (deal_id, date, speaker, transcript) VALUES ('D1', ?, 'x', 'y')", d)
    p = goals.actual_pace()
    assert p["meetings"] == 4                 # Sep 1 is outside the last 4 weeks
    assert p["meetings_pw"] == pytest.approx(1.0)
    assert goals.compare(2.25, 1.0)[0] == "behind"
