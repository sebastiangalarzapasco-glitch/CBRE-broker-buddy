# Broker Buddy

AI co-pilot for a CBRE Capital Markets broker's meeting cycle: talk through a debrief, get the deal terms, tasks and stakeholder emails, review, then send.

Built by QuadFrog Consulting (TCU BIS Capstone, Team 1). All data is synthetic.

## How it is built

| Layer | What it does | Technology |
|---|---|---|
| 1 Capture | Voice to text | Browser dictation (Web Speech API), typed notes |
| 2 Intelligence | Reads the debrief, returns facts with exact quotes | Claude Sonnet 5.5 (most careful) or Claude Haiku 5.5 (fastest), forced structured output |
| 3 Checks and data | Verifies quotes, names and numbers; one record per deal; change log; dates and cap rates calculated in code | Python, SQLite |
| 4 Experience | Today view, deal brief, email review queue, team board, share link | Flask, HTML/CSS |

The rule: **the model proposes, code checks, a broker approves.**

## Run it on your laptop (Windows PowerShell)

```
git clone <repo-url>
cd broker-buddy
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```
Open http://localhost:8080. Without an API key it runs in **Sample mode**: the four sample debriefs work with recorded answers, clearly labelled.

Notes for Windows:
- Use `python -m pip`, not `pip`. Some TCU laptops block `pip.exe` (Application Control policy).
- Run each command on its own line.
- Mac: `source .venv/bin/activate` instead of the Activate.ps1 line, and `export NAME=value` instead of `$env:NAME="value"`.

For live mode, set your own key in your terminal before `python app.py` (never commit it, paste it into a chat or show it in a screenshot):
```
$env:ANTHROPIC_API_KEY="your-key"
```

### Demo mode (same setup as Client Update #2)

`run_demo.ps1` starts the app on Nov 4 at 1:40 PM with clean sample data, so the "In 20 min" reminder for the 2:00 PM PSA call appears:
```
.\run_demo.ps1
```
Or by hand:
```
$env:BB_TODAY="2026-11-04"; $env:BB_NOW="2026-11-04T13:40"; $env:BB_RESET_ON_START="1"
python app.py
```

## Put it on a public link (Render)

1. Push this folder to a GitHub repository.
2. On render.com: New → Blueprint → pick the repository. Render reads `render.yaml`.
3. When asked, paste your `ANTHROPIC_API_KEY`.
4. Render gives you a link like `https://broker-buddy.onrender.com`.

The free plan sleeps after 15 minutes idle (the first visit takes about 30 seconds) and resets the demo data on each restart. Open the link a minute before presenting.

## Tests

```
python -m pytest -q          # 27 tests against the answer key, no API key needed
python eval_golden.py        # scores the LIVE model against the answer key (needs a key)
python eval_golden.py turbo  # same, fastest model
```

The answer key is `docs/Broker_Buddy_Answer_Key.xlsx` (same data as `data/golden_set.json`).

## Settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Turns on live mode |
| `BB_MODEL` / `BB_MODEL_TURBO` | `claude-sonnet-5-5` / `claude-haiku-5-5` | Models for each speed |
| `BB_TODAY` | real date | Pin "today" for the demo (e.g. `2026-11-04`) |
| `BB_NOW` | real time | Pin the clock for meeting reminders (e.g. `2026-11-04T13:40`) |
| `BB_REMIND_MINUTES` | 30 | How early the meeting reminder banner appears |
| `BB_ALERT_LEAD_DAYS` | 3 | How early deadline alerts appear |
| `BB_DB_PATH` | `broker_buddy.db` | Where the SQLite file lives |
| `BB_BROKER_NAME`, `BB_BROKER_TITLE` | Alex Moreno, Senior Vice President, Capital Markets | Email signature |
| `RESEND_API_KEY`, `BB_FROM_EMAIL` | none | Real sending. Without them, approved emails are marked "Approved, not sent" |
| `BB_RESET_ON_START` | off | `1` clears demo data on every start |

## Demo script (3 minutes)

1. **New debrief** → Buyer tour, Northpoint → Process. Point at "Caught for you": the $500K correction to $750K.
2. **Review emails**: Marco's escrow email carries the wire fraud warning. Change $750,000 to $500,000, Save edits and recheck: it is blocked.
3. **Team** → assign the unowned task → open the read-only share link.
4. Click the yellow **In 20 min** banner → prep brief for the PSA call with Priya.
5. Optional: run the **Retrade call** sample → the change log shows $29.7M → $29.3M.

## Project structure

| File | What it is |
|---|---|
| `app.py` | Flask routes for every page |
| `engine.py` | Processes a debrief: extraction, checks, who gets an email, alerts |
| `ai.py` | Claude calls (extraction and email drafts) and Sample mode |
| `guards.py` | Code checks: quotes, names, money figures, wire warnings |
| `meetings.py` | Sample calendar, reminders, prep briefs (no AI) |
| `db.py` | SQLite schema and seed data |
| `fixtures.py` | Recorded answers for Sample mode and tests |
| `data/golden_set.json` | Answer key (synthetic deals, contacts, debriefs, expected results) |
| `templates/`, `static/` | HTML pages and styles |
| `tests/` | Automated tests against the answer key |

## Working as a team

1. Pull before you start: `git pull`
2. Make a branch for your change: `git checkout -b your-name/short-description`
3. Run the tests before you push: `python -m pytest -q`
4. Push and open a pull request: `git push -u origin your-name/short-description`
5. Someone else reviews and merges it into `main`.

Never commit API keys, `.env` files or `broker_buddy.db`. The `.gitignore` already skips them.

## Limitations

- Synthetic data until CBRE provides masked deal documents.
- No live CRM, calendar or market data feed yet.
- Sample mode only knows the four sample debriefs.
