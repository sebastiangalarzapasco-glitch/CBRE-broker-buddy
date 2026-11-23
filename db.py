"""SQLite storage for Broker Buddy. One record per deal that every screen reuses."""
import json
import os
import secrets
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = os.environ.get("BB_DB_PATH", str(BASE / "broker_buddy.db"))
SEED = BASE / "data" / "golden_set.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
  id TEXT PRIMARY KEY, name TEXT, address TEXT, asset_type TEXT, size REAL, size_unit TEXT,
  year_built INTEGER, occupancy REAL, seller TEXT, seller_contact TEXT, noi REAL, share_token TEXT
);
CREATE TABLE IF NOT EXISTS tenants (
  deal_id TEXT, tenant TEXT, suite TEXT, sf REAL, rent_psf REAL, annual_rent REAL, expiration TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS units (
  deal_id TEXT, unit_type TEXT, avg_sf REAL, units INTEGER, occupied INTEGER, rent REAL
);
CREATE TABLE IF NOT EXISTS contacts (
  id TEXT PRIMARY KEY, name TEXT, role TEXT, company TEXT, side TEXT, email TEXT, phone TEXT, deals TEXT
);
CREATE TABLE IF NOT EXISTS terms (
  deal_id TEXT, field TEXT, value_text TEXT, value_number REAL, value_date TEXT, status TEXT,
  evidence TEXT, debrief_id INTEGER, PRIMARY KEY (deal_id, field)
);
CREATE TABLE IF NOT EXISTS change_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT, field TEXT, old TEXT, new TEXT, debrief_id INTEGER, at TEXT
);
CREATE TABLE IF NOT EXISTS debriefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT, date TEXT, speaker TEXT, transcript TEXT,
  processed_at TEXT, seconds REAL, source TEXT, flags TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT, debrief_id INTEGER, description TEXT,
  owner_id TEXT, owner_label TEXT, side TEXT, due_date TEXT, due_text TEXT, evidence TEXT, done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS emails (
  id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT, debrief_id INTEGER, to_id TEXT, to_name TEXT, to_email TEXT,
  audience TEXT, purpose TEXT, subject TEXT, body TEXT, status TEXT, issues TEXT, wire INTEGER DEFAULT 0,
  approved_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS suggestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT, debrief_id INTEGER, kind TEXT, text TEXT, status TEXT DEFAULT 'open'
);
"""


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init(reset=False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = connect()
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 0:
        seed(con)
    con.commit()
    con.close()


def seed(con):
    g = json.loads(SEED.read_text())
    for d in g["deals"]:
        con.execute(
            "INSERT INTO deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["Deal ID"], d["Property"], d["Address"], d["Asset type"], d["Size"], d["Size unit"], d["Year built"],
             d["Occupancy"], d["Seller (CBRE client)"], d["Seller contact"], d["T-12 NOI ($)"], secrets.token_urlsafe(8)),
        )
    for t in g["rent_roll"]:
        con.execute("INSERT INTO tenants VALUES (?,?,?,?,?,?,?,?)",
                    (t["Deal ID"], t["Tenant"], t["Suite"], t["Leased SF"], t["Rent ($/SF/yr)"], t["Annual base rent ($)"],
                     t.get("Lease expiration"), t.get("Note")))
    for u in g["unit_mix"]:
        con.execute("INSERT INTO units VALUES (?,?,?,?,?,?)",
                    (u["Deal ID"], u["Unit type"], u["Avg SF"], u["Units"], u["Occupied"], u["Market rent ($/mo)"]))
    for c in g["contacts"]:
        con.execute("INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?)",
                    (c["Contact ID"], c["Name"], c["Role"], c["Company"], c["Side"], c["Email"], c["Phone"], c["Deals"]))


def golden():
    return json.loads(SEED.read_text())


def rows(sql, *args):
    con = connect()
    out = [dict(r) for r in con.execute(sql, args).fetchall()]
    con.close()
    return out


def one(sql, *args):
    r = rows(sql, *args)
    return r[0] if r else None


def execute(sql, *args):
    con = connect()
    cur = con.execute(sql, args)
    con.commit()
    lid = cur.lastrowid
    con.close()
    return lid
