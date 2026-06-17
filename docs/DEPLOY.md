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
| worker `signal-runner` | `srv-d8ncrf3tqb8s73d1q1rg` | `python runner.py` — crypto 5m sim loop. **SUSPENDED 2026-06-17** (sim retired; resume only to collect sim data) |
| cron `signal-daily` | `crn-d8o1svjeo5us738btld0` | `python daily.py` every 2h — places/resolves REAL Alpaca equity brackets + Bybit/Alpaca TradingView ideas |
| DB | Neon `withered-wind-02493492` / `neondb` | `DATABASE_URL` (pooled, us-west-2) |

`db.py` auto-detects backend: `postgresql://…` → Neon, else local SQLite. Render
workspace: jae's (owner `tea-d8e1tae8bjmc73am2g10`); API auth via the
`RENDER_API_KEY` env, header `Authorization: Bearer`.

### Env vars per service (names only — set in the Render dashboard)
- **dashboard**: `DATABASE_URL`, `ADMIN_USER`, `ADMIN_PASSWORD` (gates `/admin`; 503 if unset), `SIZE_USD`, `FEE_BPS`.
- **dashboard** also reads `IDEA_RISK_USD` (risk-normalised ideas P&L, default $100) + `IDEA_NOTIONAL`.
- **worker** (`signal-runner`, **suspended**): `DATABASE_URL`, `ALPACA_KEY/SECRET`, etc. `ALPACA_PLACE_ORDERS` is now **0** — the long-only Alpaca-crypto round-trip (`alpaca_exec.py`) was retired (spread-bled, long-only). Crypto execution moved to Bybit. Resume the worker only to collect sim research data (`LAB_SIM=1`).
- **cron** (`signal-daily`): `DATABASE_URL`, `ALPACA_KEY/SECRET`, `KALSHI_*`, `PYTHONUNBUFFERED`, plus:
  - **Bybit demo crypto/gold** (`bybit_orders.py`, the active crypto venue): `BYBIT_DEMO_KEY`, `BYBIT_DEMO_SECRET`, `BYBIT_DEMO_ORDERS=1`. Sizing `BYBIT_SIZE_MODE` (`notional` demo ≈ `BYBIT_NOTIONAL` $100/trade · `risk` live ≈ `RISK_CAD` × `CAD_USD`).
  - **Alpaca equity brackets** (`equity_orders.py`): `ALPACA_EQUITY_ORDERS=1`, `ALPACA_EQUITY_STRATEGIES=gaptrav_tight_eq_1h,gaptrav_eq_1h,far_targets_eq_1h`, `ALPACA_EQUITY_QTY` (default 1).
  - **`LAB_SIM`** (default off) — re-enable Kalshi/equity-bar sim collection only if you want sim research data again.

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

## TradingView Ideas (run by hand, 1–2×/day in Claude Code)
One entry-point: **`python tradingview_ideas.py <scrape|vision|set|run|show|all>`**
(implementation in the `ideas/` package). `scrape` pulls community ideas off
TradingView (Tavily `/extract` on the listing feeds), derives each chart-image URL
from the slug, extracts params, stores them in the **`ideas`** table; the dashboard
shows them at the top via `/api/ideas`. Vision (reading levels off the chart) is
**manual** — Claude Code reads the images and writes levels back (`set`); set
`ANTHROPIC_API_KEY`/`NVIDIA_API_KEY` to automate later. `run` executes the
chart-read brackets on **real Binance public data** (`binance_sim`: limit/stop
order at the author's entry, fill + resolve TP/SL on 1m klines, long+short) —
keyless, any region. Full procedure: [tradingview_automation_run.md](tradingview_automation_run.md).
Needs `TAVILY_API_KEY` (`BINANCE_REST` optional, defaults to `api.binance.com`).
`daily.py` calls the execute step every cron cycle so resting orders fill/resolve
unattended. Paper/demo only; **≤20 concurrent open trades**, **any timeframe**.

## DB schema (created on first `db.init()`)
`signals · bets · trades · executions · experiments · strategy_versions · lessons`
· `ideas` (created lazily by `ideas_mvp.py`)

## Dependencies
`pandas, numpy, flask, gunicorn, psycopg2-binary, scikit-learn, cryptography`
(`requirements.txt`). Add new deps there **and** note them here.

## Live trading (P5, not enabled)
All strategies are paper. Real orders refuse unless a human sets
`LIVE_BUDGET_ARMED=1` plus caps + kill switch (rails in `executor.py` /
`forex_oanda.py` / `kalshi_paper.place_live`) — by design, off.
