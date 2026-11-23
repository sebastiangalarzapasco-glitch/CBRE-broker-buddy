"""Broker Buddy web app (Flask)."""
import datetime as dt
import json
import os

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for

import ai
import bov
import db
import engine
import guards
import meetings

app = Flask(__name__)
app.secret_key = os.environ.get("BB_SECRET", "broker-buddy-dev")
db.init(reset=os.environ.get("BB_RESET_ON_START") == "1")
meetings.ensure_table()
bov.ensure_table()

TEAM = ["C01", "C02", "C03"]


@app.context_processor
def globals_():
    return {
        "live": ai.live_mode(), "speaker": engine.SPEAKER, "today": engine.today(), "fmt_date": engine.fmt_date,
        "review_count": db.one("SELECT COUNT(*) n FROM emails WHERE status='Draft'")["n"],
        "nav": request.endpoint,
        "reminder": meetings.reminder(),
        "client_logo": client_logo(),
    }


def client_logo():
    """Drop a logo file at static/brand/client-logo.png (or .svg) once the client approves it."""
    for ext in ("svg", "png"):
        if os.path.exists(os.path.join(app.static_folder, "brand", f"client-logo.{ext}")):
            return url_for("static", filename=f"brand/client-logo.{ext}")
    return None


def deal_view(d):
    t = engine.current_terms(d["id"])
    d = dict(d)
    d["terms"] = [{"field": f, "label": engine.LABELS[f], "value": engine.fmt(f, t[f]), "status": t[f]["status"], "evidence": t[f]["evidence"]}
                  for f in engine.ORDER if f in t]
    price = t.get("price")
    d["headline_price"] = engine.fmt("price", price) if price and price["status"] == "agreed" else "No agreed price"
    d["stage"] = ("Under contract" if "dd_start_date" in t else "LOI signed" if price and price["status"] == "agreed" else
                  "Listing pitch" if "seller_price_expectation" in t else "New")
    d["open_actions"] = db.one("SELECT COUNT(*) n FROM actions WHERE deal_id=? AND done=0", d["id"])["n"]
    return d


@app.route("/")
def today():
    al = [a for a in engine.alerts() if a["days"] >= -7]
    soon = [a for a in al if a["days"] <= 14]
    later = [a for a in al if a["days"] > 14][:8]
    tasks = {}
    for cid in TEAM:
        c = db.one("SELECT * FROM contacts WHERE id=?", cid)
        tasks[c["name"]] = db.rows("SELECT a.*, d.name deal FROM actions a JOIN deals d ON d.id=a.deal_id WHERE owner_id=? AND done=0 ORDER BY due_date IS NULL, due_date", cid)
    sugg = db.rows("SELECT s.*, d.name deal FROM suggestions s JOIN deals d ON d.id=s.deal_id WHERE s.status='open' ORDER BY s.id DESC")
    unassigned = db.rows("SELECT a.*, d.name deal FROM actions a JOIN deals d ON d.id=a.deal_id WHERE side='unassigned' AND done=0")
    deals = [deal_view(d) for d in db.rows("SELECT * FROM deals")]
    return render_template("today.html", soon=soon, later=later, tasks=tasks, sugg=sugg, unassigned=unassigned, deals=deals,
                           upcoming=meetings.upcoming(limit=4))


@app.route("/deals/<deal_id>")
def deal(deal_id):
    d = db.one("SELECT * FROM deals WHERE id=?", deal_id) or abort(404)
    return render_template("deal.html", d=deal_view(d), **deal_bundle(deal_id), shared=False)


def deal_bundle(deal_id):
    return {
        "tenants": db.rows("SELECT * FROM tenants WHERE deal_id=? ORDER BY sf DESC", deal_id),
        "units": db.rows("SELECT * FROM units WHERE deal_id=?", deal_id),
        "people": db.rows("SELECT * FROM contacts WHERE deals LIKE ? ORDER BY side DESC, name", f"%{deal_id}%"),
        "actions": db.rows("SELECT * FROM actions WHERE deal_id=? ORDER BY done, due_date IS NULL, due_date", deal_id),
        "changes": db.rows("SELECT c.*, b.date FROM change_log c LEFT JOIN debriefs b ON b.id=c.debrief_id WHERE c.deal_id=? ORDER BY c.id DESC", deal_id),
        "debriefs": db.rows("SELECT * FROM debriefs WHERE deal_id=? ORDER BY id DESC", deal_id),
        "alerts": engine.alerts(deal_id),
        "today_d": engine.today(),
    }


@app.route("/deals/<deal_id>/bov", methods=["GET", "POST"])
def deal_bov(deal_id):
    db.one("SELECT id FROM deals WHERE id=?", deal_id) or abort(404)
    if request.method == "POST":
        try:
            caps = tuple(round(float(request.form[k]) / 100, 4) for k in ("cap_low", "cap_mid", "cap_high"))
            if not all(0.02 <= c <= 0.15 for c in caps) or not caps[0] < caps[1] < caps[2]:
                raise ValueError
        except (KeyError, ValueError):
            flash("Cap rates must be between 2% and 15%, from low to high.", "error")
            return redirect(url_for("deal_bov", deal_id=deal_id))
        bov.create(deal_id, caps)
        flash("BOV updated with your cap rates.", "ok")
        return redirect(url_for("deal_bov", deal_id=deal_id))
    row = bov.latest(deal_id)
    if row is None:
        bov.create(deal_id)
        row = bov.latest(deal_id)
    b = bov.build(deal_id, (row["cap_low"], row["cap_mid"], row["cap_high"]))
    if row["cap_mid"] == bov.default_caps(b["deal"], engine.current_terms(deal_id))[1]:
        b["basis"] = bov.default_caps(b["deal"], engine.current_terms(deal_id))[3]
    return render_template("bov.html", b=b, row=row, highlights=json.loads(row["highlights"]), money=bov.fmt_money_short)


@app.route("/share/<token>")
def share(token):
    d = db.one("SELECT * FROM deals WHERE share_token=?", token) or abort(404)
    return render_template("deal.html", d=deal_view(d), **deal_bundle(d["id"]), shared=True)


@app.route("/debrief", methods=["GET", "POST"])
def debrief():
    samples = db.golden()["debriefs"]
    if request.method == "POST":
        text = request.form.get("transcript", "").strip()
        date = request.form.get("date") or engine.today().isoformat()
        if len(text.split()) < 8:
            flash("Say or type a little more: the property and what was agreed.", "error")
            return redirect(url_for("debrief"))
        try:
            bid = engine.process(text, date, request.form.get("speed", "balanced"), background=True)
        except Exception as ex:  # shown to the broker, never hidden behind a fake result
            flash(f"Could not process this debrief: {ex}", "error")
            return render_template("debrief.html", samples=samples, text=text, date=date)
        return redirect(url_for("debrief_result", bid=bid))
    return render_template("debrief.html", samples=samples, text=request.args.get("text", ""), date=request.args.get("date") or engine.today().isoformat())


@app.route("/debriefs/<int:bid>")
def debrief_result(bid):
    b = db.one("SELECT * FROM debriefs WHERE id=?", bid) or abort(404)
    flags = json.loads(b["flags"] or "{}")
    d = db.one("SELECT * FROM deals WHERE id=?", b["deal_id"])
    terms = [x for x in deal_view(d)["terms"]]
    changed_fields = {r["field"] for r in db.rows("SELECT field FROM change_log WHERE debrief_id=?", bid)}
    this_fields = {r["field"] for r in db.rows("SELECT field FROM terms WHERE debrief_id=?", bid)}
    return render_template("result.html", b=b, flags=flags, d=d, drafting=engine.drafting(bid), terms=[t for t in terms if t["field"] in this_fields],
                           changed=changed_fields, changes=db.rows("SELECT * FROM change_log WHERE debrief_id=?", bid),
                           actions=db.rows("SELECT * FROM actions WHERE debrief_id=?", bid),
                           emails=db.rows("SELECT * FROM emails WHERE debrief_id=?", bid),
                           sugg=db.rows("SELECT * FROM suggestions WHERE debrief_id=? AND kind!='superseded'", bid))


@app.route("/emails")
def emails():
    es = db.rows("SELECT e.*, d.name deal FROM emails e JOIN deals d ON d.id=e.deal_id ORDER BY e.status!='Draft', e.id DESC")
    for e in es:
        if e["status"] == "Draft":
            e["issues"] = engine.recheck_email(e["id"])  # the deal may have changed since it was drafted
        else:
            e["issues"] = json.loads(e["issues"] or "[]")
        e["blocked"] = any(i["level"] == "block" for i in e["issues"])
    return render_template("emails.html", emails=es)


@app.post("/emails/<int:eid>/save")
def email_save(eid):
    db.execute("UPDATE emails SET subject=?, body=?, updated_at=? WHERE id=? AND status='Draft'",
               request.form["subject"], request.form["body"], dt.datetime.now().isoformat(timespec="seconds"), eid)
    issues = engine.recheck_email(eid)
    flash("Saved and rechecked." + (" Fix the red items before approving." if any(i["level"] == "block" for i in issues) else ""), "ok")
    return redirect(url_for("emails") + f"#e{eid}")


@app.post("/emails/<int:eid>/approve")
def email_approve(eid):
    reviewer = request.form.get("reviewer", "").strip()
    issues = engine.recheck_email(eid)
    if any(i["level"] == "block" for i in issues):
        flash("This email has issues that must be fixed first.", "error")
    elif not reviewer:
        flash("Type your name to approve.", "error")
    else:
        e = db.one("SELECT * FROM emails WHERE id=?", eid)
        status = send(e)
        db.execute("UPDATE emails SET status=?, approved_by=?, updated_at=? WHERE id=?", status, reviewer, dt.datetime.now().isoformat(timespec="seconds"), eid)
        flash(f"Approved by {reviewer}. Status: {status}.", "ok")
    return redirect(url_for("emails") + f"#e{eid}")


def send(e):
    """Sends only through a configured email service. Otherwise says so honestly."""
    key, sender = os.environ.get("RESEND_API_KEY"), os.environ.get("BB_FROM_EMAIL")
    if not (key and sender) or e["to_email"].endswith(".example"):
        return "Approved, not sent"
    import urllib.request
    req = urllib.request.Request("https://api.resend.com/emails", method="POST",
                                 data=json.dumps({"from": sender, "to": [e["to_email"]], "subject": e["subject"], "text": e["body"]}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        return "Sent"
    except Exception:
        return "Failed"


@app.route("/team")
def team():
    people = [db.one("SELECT * FROM contacts WHERE id=?", c) for c in TEAM]
    board = {p["id"]: db.rows("SELECT a.*, d.name deal FROM actions a JOIN deals d ON d.id=a.deal_id WHERE owner_id=? ORDER BY done, due_date IS NULL, due_date", p["id"]) for p in people}
    unassigned = db.rows("SELECT a.*, d.name deal FROM actions a JOIN deals d ON d.id=a.deal_id WHERE side='unassigned' AND done=0")
    external = db.rows("SELECT a.*, d.name deal FROM actions a JOIN deals d ON d.id=a.deal_id WHERE side='external' AND done=0 ORDER BY due_date IS NULL, due_date")
    deals = db.rows("SELECT id, name, share_token FROM deals")
    return render_template("team.html", people=people, board=board, unassigned=unassigned, external=external, deals=deals)


@app.post("/actions/<int:aid>/assign")
def assign(aid):
    c = db.one("SELECT * FROM contacts WHERE id=?", request.form["owner"]) or abort(400)
    db.execute("UPDATE actions SET owner_id=?, owner_label=?, side='internal' WHERE id=?", c["id"], c["name"], aid)
    db.execute("UPDATE suggestions SET status='done' WHERE kind='assign' AND text LIKE ?", "%" + db.one("SELECT description FROM actions WHERE id=?", aid)["description"] + "%")
    flash(f"Assigned to {c['name']}.", "ok")
    return redirect(request.referrer or url_for("team"))


@app.post("/actions/<int:aid>/done")
def done(aid):
    db.execute("UPDATE actions SET done=1-done WHERE id=?", aid)
    return redirect(request.referrer or url_for("team"))


@app.post("/suggestions/<int:sid>/dismiss")
def dismiss(sid):
    db.execute("UPDATE suggestions SET status='done' WHERE id=?", sid)
    return redirect(request.referrer or url_for("today"))


@app.route("/meetings")
def meetings_page():
    return render_template("meetings.html", items=meetings.upcoming(include_past_minutes=24 * 60),
                           deals=db.rows("SELECT id, name FROM deals"), contacts=db.rows("SELECT * FROM contacts WHERE side='External' ORDER BY name"),
                           default_start=(meetings.now() + dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M"))


@app.post("/meetings/add")
def meetings_add():
    f = request.form
    if not (f.get("deal_id") and f.get("contact_id") and f.get("start")):
        flash("Pick a deal, a person and a time.", "error")
        return redirect(url_for("meetings_page"))
    mid = meetings.add(f["deal_id"], f["contact_id"], f.get("title") or "Meeting", f["start"], f.get("location", ""))
    flash("Meeting added. Its prep brief is ready.", "ok")
    return redirect(url_for("meeting_brief", mid=mid))


@app.route("/meetings/<int:mid>")
def meeting_brief(mid):
    b = meetings.brief(mid) or abort(404)
    return render_template("brief.html", **b)


@app.route("/meetings/<int:mid>/debrief")
def meeting_debrief(mid):
    b = meetings.brief(mid) or abort(404)
    m = b["m"]
    return redirect(url_for("debrief", date=m["start"][:10], text=f"Debrief, {m['deal']}. Met with {m['contact']} from {m['company']}. "))


@app.route("/how-it-works")
def how():
    return render_template("how.html")


@app.route("/sw.js")
def service_worker():
    """Served from the site root so it can make the whole app installable on a phone."""
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.post("/reset")
def reset():
    db.init(reset=True)
    meetings.ensure_table()
    bov.ensure_table()
    flash("Demo data reset. All debriefs, emails and changes were cleared.", "ok")
    return redirect(url_for("today"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
