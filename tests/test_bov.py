"""BOV (Broker Opinion of Value): the math is checked against hand calculations from the answer key.

Northpoint Logistics Center (D1): T-12 NOI $1,633,150.80, 312,000 SF.
Run: python -m pytest -q
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NOI_D1 = 1_633_150.80


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ["BB_DB_PATH"] = str(tmp_path_factory.mktemp("bov") / "t.db")
    os.environ["BB_TODAY"] = "2026-11-04"
    import db, engine, bov
    importlib.reload(db); importlib.reload(engine); importlib.reload(bov)
    db.init(reset=True)
    return db, engine, bov


def test_value_range_northpoint(mods):
    _, _, bov = mods
    b = bov.build("D1", caps=(0.0525, 0.055, 0.0575))
    v = [x["value"] for x in b["values"]]
    # 1,633,150.80 / 5.25% = 31,107,634 -> $31.1M ; / 5.50% = 29,693,651 -> $29.7M ; / 5.75% = 28,402,623 -> $28.4M
    assert v == [31_100_000, 29_700_000, 28_400_000]
    assert b["values"][1]["exact"] == pytest.approx(NOI_D1 / 0.055)
    assert b["values"][1]["per_unit"] == pytest.approx(29_700_000 / 312_000)


def test_default_midpoint_is_placeholder_without_agreed_price(mods):
    _, _, bov = mods
    b = bov.build("D1")
    assert b["caps"] == (0.0525, 0.055, 0.0575)
    assert "Placeholder" in b["basis"]


def test_rent_roll_metrics_northpoint(mods):
    _, _, bov = mods
    m = bov.build("D1", caps=(0.0525, 0.055, 0.0575))["metrics"]
    assert m["leased_sf"] == 280_800                       # 187,200 + 93,600
    assert m["occupancy"] == pytest.approx(0.9)
    assert m["annual_rent"] == pytest.approx(1_745_640)    # 1,141,920 + 603,720
    assert m["vacant_sf"] == 31_200
    assert m["top_tenant"] == "Brazos Freight Solutions"
    # rent-weighted lease term from Nov 4, 2026: Brazos to Jun 30, 2031, Lone Star to Dec 31, 2028
    expected = ((1699 / 365.25) * 1_141_920 + (788 / 365.25) * 603_720) / 1_745_640
    assert m["walt"] == pytest.approx(expected, abs=0.01)


def test_multifamily_unit_mix(mods):
    _, _, bov = mods
    m = bov.build("D3", caps=(0.05, 0.0525, 0.055))["metrics"]
    assert m["units"] == 220 and m["occupied"] == 207
    assert m["monthly_gpr"] == pytest.approx(96 * 1245 + 100 * 1525 + 24 * 1840)


def test_template_summary_passes_its_own_check(mods):
    _, _, bov = mods
    for deal in ("D1", "D2", "D3"):
        b = bov.build(deal)
        s, h = bov.template_summary(b)
        assert bov.text_issues(s + "\n" + "\n".join(h), b) == []


def test_invented_or_confidential_figures_are_caught(mods):
    db, _, bov = mods
    b = bov.build("D1", caps=(0.0525, 0.055, 0.0575))
    assert bov.text_issues("Comparable sales support $33.5M.", b)          # not a calculated figure
    db.execute("INSERT OR REPLACE INTO terms VALUES (?,?,?,?,?,?,?,?)",
               "D3", "internal_estimate", None, 40_500_000, None, "internal_confidential", "", None)
    b3 = bov.build("D3", caps=(0.05, 0.0525, 0.055))
    issues = bov.text_issues("We see value near $40.5 million.", b3)
    assert issues and "confidential" in issues[0]
    assert all("Internal estimate" not in n["label"] for n in b3["notes"])


def test_create_and_latest(mods):
    _, _, bov = mods
    bid = bov.create("D1")
    row = bov.latest("D1")
    assert row["id"] == bid and row["source"] == "template" and row["cap_mid"] == 0.055
