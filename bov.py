"""Broker Opinion of Value (BOV).

Same rule as the rest of the app: every number is calculated here, in code, from the deal record.
Claude only writes the summary paragraph and highlights, and that text is checked so it cannot
introduce a dollar figure that is not in the calculated set. Confidential internal estimates are
never used and never shown.
"""
import datetime as dt
import json

import ai
import db
import engine
import guards

SPREAD = 0.0025  # +/- 25 bps around the midpoint cap rate

# Starting point only, used when the deal has no agreed price yet. Shown as a placeholder the broker
# must confirm with CBRE research. Not market data.
PLACEHOLDER_CAP = {"Industrial": 0.055, "Office": 0.07, "Multifamily": 0.0525, "Retail": 0.065}


def ensure_table():
    con = db.connect()
    con.execute("""CREATE TABLE IF NOT EXISTS bovs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT, cap_low REAL, cap_mid REAL, cap_high REAL,
        summary TEXT, highlights TEXT, source TEXT, created_at TEXT)""")
    con.commit()
    con.close()


def round_value(v, step=100_000):
    return round(v / step) * step


def default_caps(deal, terms):
    """Midpoint = cap rate on the agreed price if there is one; otherwise a labelled placeholder."""
    cap = terms.get("calculated_cap_rate")
    if cap and cap["value_number"]:
        mid, basis = cap["value_number"], "Cap rate on the agreed price (T-12 NOI / agreed price)"
    else:
        mid = PLACEHOLDER_CAP.get(deal["asset_type"], 0.06)
        basis = "Placeholder starting assumption: confirm with CBRE research"
    mid = round(mid, 4)
    return round(mid - SPREAD, 4), mid, round(mid + SPREAD, 4), basis


def valuation(noi, size, caps):
    """Value at each cap rate, rounded to the nearest $100,000, plus value per SF or unit."""
    out = []
    for label, c in zip(("Low cap rate (high value)", "Midpoint", "High cap rate (low value)"), caps):
        exact = noi / c
        v = round_value(exact)
        out.append({"label": label, "cap": c, "exact": exact, "value": v, "per_unit": v / size if size else None})
    return out


def _years(a, b):
    return (b - a).days / 365.25


def rent_roll_metrics(tenants, size, as_of):
    leased = [t for t in tenants if t["annual_rent"]]
    leased_sf = sum(t["sf"] for t in leased)
    rent = sum(t["annual_rent"] for t in leased)
    walt = 0.0
    for t in leased:
        if t["expiration"]:
            walt += max(0.0, _years(as_of, dt.date.fromisoformat(t["expiration"][:10]))) * t["annual_rent"]
    walt = walt / rent if rent else 0.0
    top = max(leased, key=lambda t: t["annual_rent"]) if leased else None
    soon = []
    for t in leased:
        if t["expiration"]:
            exp = dt.date.fromisoformat(t["expiration"][:10])
            if exp <= as_of.replace(year=as_of.year + 2):
                soon.append({"tenant": t["tenant"], "sf": t["sf"], "expiration": t["expiration"][:10],
                             "share": t["annual_rent"] / rent if rent else 0})
    vacant = [t for t in tenants if not t["annual_rent"]]
    return {
        "kind": "commercial", "leased_sf": leased_sf, "occupancy": leased_sf / size if size else 0, "annual_rent": rent,
        "avg_rent_psf": rent / leased_sf if leased_sf else 0, "walt": walt, "tenants": len(leased),
        "top_tenant": top["tenant"] if top else None, "top_share": (top["annual_rent"] / rent) if top and rent else 0,
        "expiring": sorted(soon, key=lambda x: x["expiration"]), "vacant_sf": sum(t["sf"] for t in vacant),
    }


def unit_mix_metrics(units):
    total = sum(u["units"] for u in units)
    occ = sum(u["occupied"] for u in units)
    gpr_m = sum(u["units"] * u["rent"] for u in units)
    return {"kind": "multifamily", "units": total, "occupied": occ, "occupancy": occ / total if total else 0,
            "monthly_gpr": gpr_m, "annual_gpr": gpr_m * 12, "avg_rent": gpr_m / total if total else 0}


def risks(deal, metrics, as_of):
    out = []
    if metrics["kind"] == "commercial":
        for e in metrics["expiring"]:
            out.append(f"{e['tenant']} ({e['sf']:,.0f} SF, {e['share'] * 100:.0f}% of base rent) expires {engine.fmt_date(e['expiration'])}.")
        if metrics["vacant_sf"]:
            out.append(f"{metrics['vacant_sf']:,.0f} SF is vacant; value assumes in-place income only, no lease-up.")
        if metrics["top_share"] >= 0.5:
            out.append(f"Tenant concentration: {metrics['top_tenant']} pays {metrics['top_share'] * 100:.0f}% of base rent.")
    else:
        if metrics["occupancy"] < 0.95:
            out.append(f"Occupancy is {metrics['occupancy'] * 100:.1f}%; value assumes in-place income, no lease-up.")
    age = as_of.year - deal["year_built"]
    if age >= 15:
        out.append(f"Built {deal['year_built']} ({age} years): buyers will price in capital needs.")
    out.append("No sales comparables yet: add CBRE comps before this goes to the client.")
    return out


def build(deal_id, caps=None):
    """Everything the BOV page shows, all calculated in code. No AI here."""
    deal = db.one("SELECT * FROM deals WHERE id=?", deal_id)
    terms = engine.current_terms(deal_id)
    as_of = engine.today()
    low, mid, high, basis = default_caps(deal, terms)
    if caps:
        low, mid, high = caps
        basis = "Broker's cap rate assumptions"
    tenants = db.rows("SELECT * FROM tenants WHERE deal_id=? ORDER BY annual_rent DESC", deal_id)
    units = db.rows("SELECT * FROM units WHERE deal_id=?", deal_id)
    metrics = rent_roll_metrics(tenants, deal["size"], as_of) if tenants else unit_mix_metrics(units)
    vals = valuation(deal["noi"], deal["size"], (low, mid, high))
    expectation = terms.get("seller_price_expectation")
    exp_val = expectation["value_number"] if expectation and expectation["status"] == "expectation" else None
    notes = []
    for f in ("seller_price_expectation", "closing_date", "financing"):
        t = terms.get(f)
        if t and t["status"] not in ("not_mentioned", "internal_confidential", "needs_review"):
            notes.append({"label": engine.LABELS[f], "value": engine.fmt(f, t), "evidence": t["evidence"] if t["status"] != "calculated" else "",
                          "calculated": t["status"] == "calculated"})
    return {
        "deal": deal, "as_of": as_of, "caps": (low, mid, high), "basis": basis, "values": vals, "metrics": metrics,
        "risks": risks(deal, metrics, as_of), "notes": notes, "seller_expectation": exp_val,
        "unit_label": "per SF" if deal["size_unit"] == "SF" else "per unit",
    }


def allowed_money(b):
    """The only dollar figures the summary text may use."""
    vals = [b["deal"]["noi"]] + [v["value"] for v in b["values"]] + [v["exact"] for v in b["values"]]
    vals += [v["per_unit"] for v in b["values"] if v["per_unit"]]
    m = b["metrics"]
    vals += [m.get("annual_rent"), m.get("avg_rent_psf"), m.get("monthly_gpr"), m.get("annual_gpr"), m.get("avg_rent")]
    if b["seller_expectation"]:
        vals.append(b["seller_expectation"])
    return [v for v in vals if v]


def forbidden_money(deal_id):
    return [t["value_number"] for t in engine.current_terms(deal_id).values()
            if t["status"] == "internal_confidential" and t["value_number"]]


def text_issues(text, b):
    """Any dollar figure in the narrative must come from the calculated set, and never a confidential one."""
    allowed, banned = allowed_money(b), forbidden_money(b["deal"]["id"])
    bad = []
    for v in guards.money_values(text):
        if any(guards._near(v, f) for f in banned):
            bad.append(f"${v:,.0f} is a confidential internal estimate")
        elif not any(guards._near(v, a) for a in allowed):
            bad.append(f"${v:,.0f} is not in the calculated figures")
    return bad


def fmt_money_short(v):
    return f"${v / 1_000_000:,.1f}M" if v >= 1_000_000 else f"${v:,.0f}"


def template_summary(b):
    d, m, v = b["deal"], b["metrics"], b["values"]
    size = f"{d['size']:,.0f} SF" if d["size_unit"] == "SF" else f"{d['size']:,.0f}-unit"
    rng = f"{fmt_money_short(v[2]['value'])} to {fmt_money_short(v[0]['value'])}"
    summary = (f"{d['name']} is a {size} {d['asset_type'].lower()} property built in {d['year_built']} at {d['address']}. "
               f"On trailing 12-month NOI of ${d['noi']:,.0f} and cap rates of {b['caps'][0] * 100:.2f}% to {b['caps'][2] * 100:.2f}%, "
               f"we estimate a value of {rng}, with a midpoint of {fmt_money_short(v[1]['value'])}.")
    if m["kind"] == "commercial":
        hl = [f"{m['occupancy'] * 100:.0f}% leased to {m['tenants']} tenants, {m['walt']:.1f} years of weighted average lease term",
              f"In-place base rent of ${m['annual_rent']:,.0f} a year, averaging ${m['avg_rent_psf']:.2f} per SF",
              f"Largest tenant: {m['top_tenant']}, {m['top_share'] * 100:.0f}% of base rent"]
    else:
        hl = [f"{m['occupancy'] * 100:.1f}% occupied across {m['units']} units",
              f"Gross potential rent of ${m['annual_gpr']:,.0f} a year, averaging ${m['avg_rent']:,.0f} per unit per month",
              f"Built {d['year_built']}, with a unit mix weighted toward one and two bedrooms"]
    return summary, hl


BOV_TOOL = {
    "name": "write_bov_summary",
    "description": "Write the executive summary and investment highlights for a Broker Opinion of Value.",
    "input_schema": {"type": "object", "properties": {
        "summary": {"type": "string", "description": "2 to 3 sentences, plain professional language."},
        "highlights": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 4}},
        "required": ["summary", "highlights"]},
}


def narrative(b):
    """Claude writes the words in live mode; the template does in Sample mode or if Claude's text fails the check."""
    client = ai._client()
    if client is None:
        s, h = template_summary(b)
        return s, h, "template"
    d, m = b["deal"], b["metrics"]
    facts = {
        "property": d["name"], "address": d["address"], "asset_type": d["asset_type"], "year_built": d["year_built"],
        "size": f"{d['size']:,.0f} {d['size_unit']}", "t12_noi": f"${d['noi']:,.0f}",
        "value_range": [f"{fmt_money_short(x['value'])} at {x['cap'] * 100:.2f}% cap" for x in b["values"]],
        "income": {k: (round(val, 2) if isinstance(val, float) else val) for k, val in m.items() if k not in ("expiring", "kind")},
        "risks": b["risks"],
    }
    prompt = ("Write the executive summary and 3 investment highlights for a Broker Opinion of Value. Use ONLY the facts below. "
              "Do not add any number, dollar amount, comparable sale, market statistic or name that is not listed. "
              "Do not promise or guarantee a price.\n\n" + json.dumps(facts, indent=1))
    try:
        out = ai._call_tool(client, ai.model_for("turbo"), BOV_TOOL, [{"role": "user", "content": prompt}], 700)
        text = out["summary"] + "\n" + "\n".join(out["highlights"])
        if not text_issues(text, b):
            return out["summary"], out["highlights"], "claude"
    except Exception:
        pass
    s, h = template_summary(b)
    return s, h, "template"


def create(deal_id, caps=None):
    ensure_table()
    b = build(deal_id, caps)
    summary, highlights, source = narrative(b)
    bid = db.execute("INSERT INTO bovs (deal_id, cap_low, cap_mid, cap_high, summary, highlights, source, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     deal_id, *b["caps"], summary, json.dumps(highlights), source, dt.datetime.now().isoformat(timespec="seconds"))
    return bid


def latest(deal_id):
    ensure_table()
    return db.one("SELECT * FROM bovs WHERE deal_id=? ORDER BY id DESC LIMIT 1", deal_id)
