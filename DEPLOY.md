# Deploy to Render + Neon

## Overview
- **Neon** = the Postgres database (free tier, serverless). Holds signals/bets/trades.
- **Render** = hosting. Two services:
  - `signal-dashboard` (web) — the monitor UI + API, runs under gunicorn.
  - `signal-runner` (worker) — the 5m live loop writing to the DB.

`db.py` auto-detects: if `DATABASE_URL` is a postgres:// string it uses Neon,
otherwise it falls back to a local SQLite file. Same code locally and in prod.

## 1. Neon (database)
1. neon.tech -> new project. Pick a region near your Render region.
2. Copy the **pooled** connection string (Dashboard -> Connection Details ->
   "Pooled connection"). It looks like:
   `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require`
   Use the POOLED string — the runner + web + analyst open separate connections.
3. That string is your `DATABASE_URL`.

## 2. Render (hosting)
Push this folder to a GitHub repo (the .gitignore keeps data/secrets/db out).
Then Render -> New -> Blueprint -> select the repo. render.yaml provisions both
services. In each service's Environment, set:
  - `DATABASE_URL`     = the Neon pooled string  (both web + worker)
  - `NVIDIA_API_KEY`   = NIM key                  (worker, for analyst/assist)
  - `ANTHROPIC_API_KEY`= Claude key               (worker, for daily Fable report)
First boot runs `db.init()` automatically (web service on import, runner on start).

### Important: the worker plan is paid
Render runs background workers only on a paid plan (~$7/mo). Two options:
  - **A (recommended):** pay for the worker so the 5m loop runs 24/7.
  - **B (free):** drop the worker; instead trigger one cycle on a schedule with
    Render **Cron Jobs** (free) running `python runner.py --once` every 5 min:
    `*/5 * * * *`. Slightly less precise on the boundary but free. The dashboard
    web service stays on the free tier either way.

### Binance reachability from Render
Render's US regions may hit Binance geo-blocks (HTTP 451). Use **Frankfurt** or
**Singapore** region for the worker/cron, or point the data fetch at
`data.binance.vision` (already the fallback used by data.py). Test after deploy
with the runner logs (look for `binance error`).

## 3. Verify
- Web service URL (Render gives you one) -> the dashboard loads, panels show
  "No bets yet" until the runner logs the first resolved window.
- Worker logs -> a line per 5m cycle; `BET ...` / `TRADE ...` when signals fire.
- DB sanity: `psql "$DATABASE_URL" -c "select strategy,count(*) from signals
  join ... group by 1"` or just watch the dashboard counts climb.

## 4. Secrets hygiene
- Never commit `.env`, `*.db`, `*.csv` (covered by .gitignore).
- Polymarket/OANDA live keys (`POLY_PRIVATE_KEY`, `OANDA_TOKEN`) go in Render
  env vars too, but ONLY when you flip from paper to live — and on a dedicated
  wallet/demo account per the rails in executor.py / forex_oanda.py.

## 5. Local dev still works
No DATABASE_URL -> SQLite. `python3 runner.py --probe`, `python3 dashboard_db.py`
(serves on PORT or 8050). Identical behavior, file-based DB.

## Files Render/Neon use
| file | role |
|---|---|
| render.yaml | provisions web + worker |
| Procfile | fallback process defs (if not using the blueprint) |
| requirements.txt | gunicorn + psycopg2 + the stack |
| db.py | Neon/SQLite dual backend |
| dashboard_db.py | gunicorn entry: `dashboard_db:app` |
| runner.py | worker / cron entry (`--once` for cron) |
