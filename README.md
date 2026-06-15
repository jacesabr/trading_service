# trading_service

An autonomous **strategy research lab** + live paper-tracking system. It discovers,
validates (leak-test + walk-forward, NO-LOOKAHEAD enforced by the harness),
paper-tracks, and prunes short-horizon signals across crypto (Binance),
prediction markets (Kalshi), and equities (Alpaca) — with options/weather as
future domains. Everything is **paper**; live trading is gated off by design.

## Read these first
1. **CLAUDE.md** — project guide (the cardinal NO-LOOKAHEAD rule, status, key files)
2. **docs/INFRA.html** — the living architecture doc (also served at `/docs`)
3. **STRATEGY.md** — the error audit + what the live data actually showed
4. **daily_run.md** — the daily research procedure (run by hand in Claude Code)
5. **DEPLOY.md** — local dev + Render/Neon prod runbook

## Quick start (local, free, SQLite)
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python db.py                 # init SQLite (or Neon if DATABASE_URL is set)
python lab.py list           # lifecycle + P&L of every strategy
python daily.py              # resolve + collect Kalshi & equities (paper)
python dashboard_db.py       # http://localhost:8050  (/admin, /docs)
```

## How it's organized
- **lab.py** — the CLI you operate the lab through (create/validate/promote/retire).
- **rlab/** — registry (manifests + lifecycle) + harness (leak/walk-forward/grid).
- **strategies.py** + helpers (gap_traversal, indicator_battery, zone_breaks, …) —
  the validated crypto signal functions.
- **runner.py** (crypto 5m), **daily.py** (Kalshi + equities, every 2h on cron),
  **kalshi_paper.py**, **equity_paper.py**, **adapters/** — the live paper engines.
- **dashboard_db.py** — public results sorted by P&L, `/admin` (auth), `/docs`.
- **db.py** — Neon/SQLite store (signals/bets/trades/executions + experiments/
  strategy_versions/lessons).

## Status (live paper, not backtest)
Live paper refuted the original backtest ranking: **meanrev was demoted** (45.9%
live vs 54-57% backtest), and zone strategies (gaptrav / far_targets) look
strongest on **higher timeframes**. The dashboard shows current P&L per strategy;
the `lessons` table records what was tried and rejected. See CLAUDE.md / STRATEGY.md.

## Live trading
Off by design. Real orders refuse unless `LIVE_BUDGET_ARMED=1` + caps + kill
switch (rails in executor.py / forex_oanda.py / kalshi_paper.place_live).
