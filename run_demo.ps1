# Starts Broker Buddy in demo mode: Nov 4, 2026 at 1:40 PM, clean sample data.
# The "In 20 min" reminder for the 2:00 PM PSA call with Priya appears on every page.
# Set $env:ANTHROPIC_API_KEY first for live Claude; otherwise Sample mode is used.
$env:BB_TODAY = "2026-11-04"
$env:BB_NOW = "2026-11-04T13:40"
$env:BB_RESET_ON_START = "1"
if (Test-Path ".venv\Scripts\python.exe") { & ".venv\Scripts\python.exe" app.py } else { python app.py }
