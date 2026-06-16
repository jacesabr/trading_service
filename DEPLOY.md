# DEPLOY.md — deployment & runbook

Single source for running this locally and in prod. Architecture lives in
[docs/INFRA.html](docs/INFRA.html); the daily research procedure in
[daily_run.md](daily_run.md). Secrets are referenced by NAME only — real values
live in the gitignored `.env` / `secrets/` and in each Render service's env.

## Local dev
```bash
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
python db.py                       # init/verify the store (SQLite if no DATABASE_URL)
python lab.py list                 # lifecycle + P&L of every strategy
python daily.py                    # resolve + collect Kalshi & equities (paper)
python runner.py --probe           # crypto connectivity check
```
`.env` (gitignored) holds local secrets: `KALSHI_API_KEY_ID`,
`KALSHI_PRIVATE_KEY_PATH` (→ `secrets/kalshi_rsa.pem`), `KALSHI_API_BASE`,
`ALPACA_KEY`, `ALPACA_SECRET`. Set `DATABASE_URL` to use prod Neon instead of local
SQLite. Crypto data: `python data.py BTCUSDT 5m 1m 2025-06 2026-05`.

## Prod topology (Render + Neon, GitHub `jacesabr/trading_service` @ master)
| Service | ID | Role |
|---|---|---|
| web `signal-dashboard` | `srv-d8ncr4k8aovs73ab7bb0` | `gunicorn dashboard_db:app` — https://signal-dashboard-3rzj.onrender.com |
| worker `signal-runner` | `srv-d8ncrf3tqb8s73d1q1rg` | `python runner.py` — crypto 5m loop (frankfurt; Binance blocks US IPs) |
| cron `signal-daily` | `crn-d8o1svjeo5us738btld0` | `python daily.py` every 2h (`30 */2 * * *`) — Kalshi + equities |
| DB | Neon `withered-wind-02493492` / `neondb` | `DATABASE_URL` (pooled, us-west-2) |

`db.py` auto-detects backend: `postgresql://…` → Neon, else local SQLite. Render
workspace: jae's (owner `tea-d8e1tae8bjmc73am2g10`); API auth via the
`RENDER_API_KEY` env, header `Authorization: Bearer`.

### Env vars per service (names only — set in the Render dashboard)
- **dashboard**: `DATABASE_URL`, `ADMIN_USER`, `ADMIN_PASSWORD` (gates `/admin`; 503 if unset), `SIZE_USD`, `FEE_BPS`.
- **worker**: `DATABASE_URL`, `NVIDIA_API_KEY`, `ALPACA_KEY`, `ALPACA_SECRET`, `PYTHONUNBUFFERED`. **Real filled demo trades** (`alpaca_exec.py`): `ALPACA_PLACE_ORDERS=1` arms real Alpaca paper orders — long-crypto round-trips (buy on signal, sell to flatten next bar) recorded in `executions` with real fills + P&L. Allow-list `ALPACA_ORDER_STRATEGIES` (default `clv_fade`), `ALPACA_ORDER_NOTIONAL` (default 12, >$10 crypto floor), `ALPACA_MAX_HOLD_S` (default 1800 force-flatten). Self-closing + deduped, so safe to leave armed; unset → Phase-1 quote-cross sim. Money floor unaffected (paper account; real money still needs `LIVE_BUDGET_ARMED`).
- **cron**: `DATABASE_URL`, `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY` (PEM contents), `KALSHI_API_BASE`, `ALPACA_KEY`, `ALPACA_SECRET`, `PYTHONUNBUFFERED`.

### Deploying a change
Auto-deploy is **OFF** (Render pulls the public repo but there's no GitHub
webhook). After `git push origin master`, trigger each affected service:
```bash
curl -s -X POST -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/<service-id>/deploys" -d '{"clearCache":"do_not_clear"}'
```
Redeploy the **dashboard** to pick up new strategy manifests (`rlab/registry/*.json`)
so new cards appear. Binance geo-block (HTTP 451) on US regions → keep the worker
in **frankfurt** (or use `data.binance.vision`, already data.py's source).

## DB schema (created on first `db.init()`)
`signals · bets · trades · executions · experiments · strategy_versions · lessons`

## Dependencies
`pandas, numpy, flask, gunicorn, psycopg2-binary, scikit-learn, cryptography`
(`requirements.txt`). Add new deps there **and** note them here.

## Live trading (P5, not enabled)
All strategies are paper. Real orders refuse unless a human sets
`LIVE_BUDGET_ARMED=1` plus caps + kill switch (rails in `executor.py` /
`forex_oanda.py` / `kalshi_paper.place_live`) — by design, off.
