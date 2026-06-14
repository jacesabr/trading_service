# trading_service

Short-horizon BTC/ETH signal research + live paper-tracking. Polymarket 5m
binary (mean-reversion) + forex gap-traversal (paper experiment). Postgres/Neon
or SQLite, Flask dashboard, daily LLM analysis.

## Read these first
1. CLAUDE.md          — project guide for Claude Code (no-lookahead rules, status)
2. STRATEGY.md        — what works, what's dead, and the error audit
3. RENDER_DEPLOY.md   — deploy to Render + Neon (step by step)
4. SYSTEM.md          — architecture and components

## Status of the two strategies
- meanrev (strategies.py): RSI/MFI 5m -> Polymarket binary. 54-57% leak-free.
  LIVE candidate, runs in PAPER mode (logs real book fills, no capital).
- gaptrav (gap_traversal.py): gap-close -> next-zone touch. ~breakeven after
  costs. PAPER experiment only.
Everything else tested (wick fade, far targets, spike-retrace, zone-bias) is
documented DEAD in CLAUDE.md / STRATEGY.md — don't re-litigate.

## Quick start (local, free, SQLite)
    python3 -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    python3 data.py BTCUSDT 5m 1m 2025-11 2026-06
    python3 data.py ETHUSDT 5m 1m 2025-11 2026-06
    python3 db.py                 # init SQLite (or Neon if DATABASE_URL set)
    python3 runner.py --probe     # connectivity check (needs Binance reach)
    python3 dashboard_db.py       # http://localhost:8050

## Deploy
See RENDER_DEPLOY.md. ~$7/mo (Render worker) + free dashboard + free Neon.
Worker MUST run in a non-US region (Binance geo-blocks US IPs).

## Entry points
- runner.py        live loop (--probe / --once / default loop)
- dashboard_db.py  monitor (gunicorn dashboard_db:app)
- analyst.py       daily LLM report (--dry to preview)
- backtests:       gap_traversal.py, indicator_battery.py, mr_5m.py,
                   gap_next5m.py (the leak test), confluence_search.py

## Live trading (only after paper gates pass — see PLAN.md)
executor.py (Polymarket CLOB) / forex_oanda.py (OANDA practice) — both behind
confirm_live=True gates with size caps. Do not loosen.
