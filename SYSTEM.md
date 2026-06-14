# Live System — Architecture, Deployment, Service Path

## What's tracked, and the honest status of each

| Strategy | Market | Backtest (held-out) | Status |
|---|---|---|---|
| `meanrev` — RSI/MFI 5m mean-reversion | Polymarket 5m Up/Down | 54–57% on close, leak-free | **LIVE candidate** |
| `gaptrav` — gap→next-zone traversal | Forex/CFD SL-TP | 58–61% touch, but ~0 net expectancy after spread | **Paper experiment only** |

The expectancy optimization (target/stop grid, both symbols) could not find a
gap-traversal config that clears even a 1 bps spread — best was −1.1 to −1.4 bps
gross. So it is wired in as a *tracked paper experiment*, not a funded strategy.
It stays in the system because a flat backtest occasionally hides a tradeable
sub-condition that only live data surfaces; if its live curve diverges upward
from the backtest, that's the signal to investigate — not before.

meanrev is the only strategy that is both leak-free and positive in the terms
its market actually pays (binary close, no spread — only the entry-price
question). It is the one worth taking live, at small size, after the paper gate.

## Components
```
data.py            historical Binance dumps (backtests, monthly re-validation)
strategies.py      BOTH signals, one import — single source of truth
  meanrev_signal() leak-free 5m close predictor (rules.json)
  gaptrav_open()   gap-close -> SL/TP trade descriptor
indicator_battery  monthly re-validation of meanrev rules on fresh data
gap_traversal.py   gaptrav backtest + zone construction
db.py              SQLite store (signals / bets / trades) — real-time tracking
runner.py          live loop: signals -> DB -> resolve, both strategies
paper_trader.py    Polymarket market discovery + order-book helpers (reused)
forex_oanda.py     OANDA practice connector for gaptrav (paper + live-demo)
executor.py        Polymarket CLOB executor (paper + live, maker-only, rails)
dashboard_db.py    DB-backed monitor, both strategies side by side (:8050)
analyst.py         daily Fable/NIM report over the DB
llm_client.py      provider-agnostic LLM (Anthropic + NIM)
```

## Data flow
```
Binance 5m/1m ──> strategies.py ──> runner.py ──┬─> db.bets   (meanrev/Polymarket)
                                                └─> db.trades (gaptrav/forex)
                         Polymarket CLOB book ───┘
   db.sqlite ──> dashboard_db.py (:8050)  +  analyst.py (daily LLM report)
```

## Deploy (one machine, reachable to api.binance.com)
```bash
python3 -m venv venv && source venv/bin/activate
pip install pandas numpy flask scikit-learn
python3 data.py BTCUSDT 5m 1m 2025-11 2026-06   # warmup + monthly re-validation
python3 data.py ETHUSDT 5m 1m 2025-11 2026-06
python3 db.py                                    # create tracker.db
python3 runner.py --probe                        # verify connectivity
pm2 start runner.py       --name trk-runner --interpreter ./venv/bin/python3
pm2 start dashboard_db.py --name trk-dash   --interpreter ./venv/bin/python3
pm2 start analyst.py      --name trk-analyst --interpreter ./venv/bin/python3 --cron "30 0 * * *" --no-autorestart
pm2 save && pm2 startup
```
Phone access: Tailscale on server + phone, open http://<tailscale-ip>:8050.

## Trading platforms (when paper gates pass)
- **Polymarket (meanrev):** real orders via `executor.py` (py-clob-client),
  maker-only, $20/order cap + $60 daily-loss kill switch, dedicated wallet.
  Promotion gate (from PLAN.md §4): 400+ resolved bets, live hit within 2pp of
  backtest, AND avg entry price below hit rate. Verify the one-time wallet/
  allowance setup with a single $5 manual order before trusting the loop.
- **Forex (gaptrav):** `forex_oanda.py` against a free OANDA practice account
  (real demo API, not real money). Given ~0 backtested expectancy, this stays
  on the practice account indefinitely unless live data shows a real edge.

## Selling it as an information service — concrete, honest
Build order: **verifiable track record first**, product second.
1. Run the DB live for 3+ months. On-chain Polymarket trades are independently
   verifiable — that public, timestamped record IS the product's credibility.
2. Surface signals as a feed (the dashboard is the seed; add a read-only public
   view + a Telegram/Discord push from the same DB rows).
3. Pricing must respect **capacity**: observed best-ask depth ~150–400 shares
   means the meanrev edge supports only low-hundreds-$ per window; subscribers
   trading the same signal move the book against each other. Cap seats — an
   uncapped signal service destroys its own edge. This is structural, not
   conservative.
4. **Decay**: meanrev is a public, fee-taxed micro-effect; assume it shrinks.
   Sell the *ongoing research + tracking*, not a frozen rule. The monthly
   re-validation (indicator_battery) and daily analyst are the actual product.
5. **Compliance**: paid trading signals for prediction markets sit in a
   jurisdiction-dependent gray zone (Polymarket itself geo-restricts). Frame as
   educational/informational, no performance guarantees, log everything, and
   get real legal advice before charging. This is not optional.

A cleaner-economics alternative: publish the verified track record + research
as content (the same audience you want for TradingView/consulting) and keep the
trading P&L as the product — no capacity ceiling, no signal cannibalization, no
gray-zone compliance exposure.
