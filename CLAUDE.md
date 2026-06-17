# CLAUDE.md — Project guide for Claude Code

Autonomous strategy research lab + live paper-tracking system. Read this first,
then **docs/INFRA.html** for the living architecture, **docs/STRATEGY.md** for the
error audit, and **daily_run.md** for the daily research procedure.

## ⭐ Current state (2026-06-17) — REAL broker demo, local sim retired
The system no longer self-reports fills from klines/bars. Every tracked trade is
placed on a real broker **demo/paper** API (broker-confirmed entry, exit & P&L):
- **Crypto + gold** (XAUUSD/GOLD → PAXGUSDT) → **Bybit demo** (`api-demo.bybit.com`,
  hedge mode, per-idea LIMIT entry + reduce-only conditional TP/SL, long **and**
  short). Code: **bybit_orders.py**. Keys `BYBIT_DEMO_KEY/SECRET`, arm with
  `BYBIT_DEMO_ORDERS=1`.
- **US equities** → **Alpaca paper** bracket OCO (**equity_orders.py**, `ALPACA_*`).
- `binance_sim` is now only a dev fallback when Bybit is unarmed.
- The self-resolved **SIM lab is RETIRED**: all sim `bets`/`trades` were deleted,
  `daily.py` gates sim collection behind `LAB_SIM` (default off), and the
  `signal-runner` worker is **suspended**. The dashboard shows **real fills only**.
- **Sizing:** demo = ~$100/trade notional (`BYBIT_SIZE_MODE=notional`). For the LIVE
  goal of **$1 CAD risk/trade**, flip `BYBIT_SIZE_MODE=risk` + `RISK_CAD` — only
  viable on %-fee venues (no flat/overnight fees); Bybit perps fit. Live venue
  research is in docs/DEPLOY.md.
- meanrev/Polymarket is **demoted** (live paper refuted it — see bottom). Treat the
  historical "Strategies"/"Settled negative" sections below as the research record,
  not current execution.

## What this project is
A self-operating lab that discovers, validates, paper-tracks, and (gated, off)
could live-trade short-horizon signals across many markets: crypto (Binance),
prediction markets (Kalshi — the live-API venue replacing Polymarket; Polymarket
paper kept), equities (Alpaca paper), with options/weather as future domains.
A strategy is a manifest (`rlab/registry/*.json`) + a signal fn; the lab CLI
(`lab.py`) creates/validates/promotes/retires them; the harness (`rlab/harness.py`)
enforces NO-LOOKAHEAD. Everything is **paper** (money floor off by design).

## The cardinal rule of this codebase: NO LOOKAHEAD
Every signal must be computable from data available BEFORE the outcome bar.
This has bitten us repeatedly. Three guards, never remove them:
1. Features lag by one bar in backtests (`features(df, lag=True)`); live uses
   `lag=False` to read the just-closed bar. They must represent the SAME thing.
2. For Polymarket: the bet candle must open AFTER the signal fires. Predicting
   a candle whose formation overlaps the signal is leakage (we measured a fake
   63% that was really 50% on the un-formed remainder — see legacy/gap_next5m.py).
3. Validation: TRAIN/VALIDATION/TEST split chronologically; TEST read ONCE.
   New rules prove out on TEST and on BOTH symbols, or they don't ship.

When adding any signal, write the leak test FIRST (predict the remainder /
the next independent bar). If it's ~50% leak-free, it does not work — no matter
how good the in-sample number looks.

## Strategies (status matters — don't conflate)
- `meanrev` (strategies.py): RSI/MFI 5m mean-reversion -> Polymarket. 54-57% on
  close, leak-free. LIVE candidate. Rules in rules.json (edit JSON, not code).
- `gaptrav` (gap_traversal.py): gap-close -> next-zone touch. ~58-68% touch but
  ~0 net expectancy after spread. PAPER experiment.
- `gaptrav_tight` (TODO): gaptrav target + stop just beyond the entry wick.
  68% win, breakeven 55%, ~-1 to -5 bps. Most promising untested-live config.
- `meanrev_confluence` (TODO): meanrev as a filter on gaptrav entries. Testing
  whether confluence beats either parent. Separate DB strategy for clean numbers.

## Key files
- **lab.py** — the guarded CLI (list/status/new/leaktest/backtest/walkforward/
  gridsearch/tweak/promote/retire/lesson). The single interface for operating the
  lab; lifecycle guards live here.
- **rlab/** — `registry.py` (manifest loader + lifecycle + legacy merge),
  `harness.py` (leak test / walk-forward / grid — enforces NO-LOOKAHEAD),
  `registry/*.json` (one manifest per strategy), `impl/*.py` (agent-authored sigs).
- strategies.py — the original validated signal fns (single source for the
  migrated crypto strategies; manifests point here).
- db.py — Neon/SQLite store: signals/bets/trades/executions + experiments/
  strategy_versions/lessons. record_*/resolve_*/record_signals_trades (bulk)/stats.
- runner.py — crypto 5m live loop. daily.py — every-2h Kalshi+equity collect/resolve.
- kalshi_paper.py + adapters/ (kalshi_client, data/kalshi, data/alpaca) — real-API
  paper engines. equity_paper.py — strategy battery on Alpaca equities × timeframes.
- dashboard_db.py — public results (sorted by P&L) + `/admin` (auth) + `/docs`
  + `/api/ideas` and the TradingView Ideas board at the top of the page.
- **tradingview_ideas.py** — THE single root entry-point for the TradingView Ideas
  pipeline (scrape → chart-read → demo-execute). Subcommands: `scrape`, `vision`,
  `set`, `run`, `show`, `all`. Run 1–2×/day per **tradingview_automation_run.md**.
- ideas/ (package) — implementation behind the entry-point:
  - ideas/scrape.py — P1–P2: scrape via Tavily + store in the `ideas` table +
    extract levels. Vision is **manual** — Claude Code reads the chart images and
    writes levels back (`set`); the VLM path is wired for later.
  - ideas/execute.py — P3 demo execution: route each idea by asset and place a REAL
    broker demo order at the author's entry, broker holds the TP/SL. Crypto+gold →
    **Bybit demo** (bybit_orders.py); US equities → **Alpaca paper** bracket
    (equity_orders.py); `binance_sim` only as a dev fallback. Lifecycle in
    `ideas.status`: extracted→pending→open→resolved (+ invalidated/no_venue/expired).
    `--migrate-bybit` moves an existing book onto Bybit.
- bybit_orders.py — REAL Bybit demo crypto/gold venue (the active crypto path).
  equity_orders.py — REAL Alpaca paper equity brackets.
- executor.py / forex_oanda.py / kalshi_paper.place_live — older live order layers,
  all gated (LIVE_BUDGET_ARMED). Don't loosen rails.
- LEGACY one-off backtests (superseded by lab.py/harness, kept for reference, now
  in **legacy/**, run via `python -m legacy.<name>`): legacy/gap_next5m.py,
  legacy/backtest.py, legacy/trade_backtest.py, legacy/mr_5m.py. Still-imported
  helpers (stay at root): gap_traversal.py, indicator_battery.py, zone_breaks.py,
  confluence_search.py, bias_test.py (do not delete — strategies.py depends on them).
- Repo layout: root holds the live app modules + entry points (runner/daily/
  dashboard_db/lab/data/tradingview_ideas) + the two runbooks; docs/ (DEPLOY.md,
  STRATEGY.md, INFRA.html), legacy/, tests/, data/ (gitignored dumps), adapters/,
  ideas/, rlab/ hold the rest. Flat imports — don't move root modules without
  refactoring imports + render.yaml/Procfile.

## Data
`python3 data.py SYMBOL CHARTTF LTF START_YM END_YM` -> SYMBOL_TF.csv (Binance
dumps, no API key). Zones need a lower TF than the chart (5m zones need 1m).

## Conventions
- Don't add a dependency without noting it in DEPLOY.md + requirements.txt.
- bps = basis points of price return; "expectancy" is always net-of-nothing
  gross unless a fee/spread is named. Polymarket EV is per-$1-staked.
- Sizes: Polymarket min 5 shares (not $5); dollar cost = 5 * price. Configurable
  in runner.py SIZE_USD. Forex paper sizes in forex_oanda.
- Never commit tracker.db or *.csv data dumps.

## Safe local commands (no live trading, no keys needed)
```bash
python db.py                          # init/verify DB
python lab.py list                    # lifecycle + P&L of every strategy
python lab.py backtest meanrev        # leak-safe backtest via the harness
python lab.py leaktest <name>         # the leak test (run for any new idea)
python daily.py                       # resolve + collect Kalshi & equities (paper)
python runner.py --probe              # crypto connectivity check
```
See **daily_run.md** for the full daily research procedure.

## When asked to "improve the strategy"
1. Form the rule. 2. Write its leak test. 3. Backtest with TRAIN/VAL/TEST.
4. Report edge vs the RANDOM-WALK / unconditional baseline, not vs 50%.
5. If it survives, add to strategies.py + rules.json + a DB strategy tag.
Never auto-apply an LLM-suggested rule to live trading — log it, paper it,
let the DB decide. The analyst (analyst.py) proposes; humans approve.

## Settled negative results — do NOT re-litigate these
These were tested thoroughly and failed. Re-running them wastes time; if you
think you have a new angle, the bar is a walk-forward that's positive in >=4/5
time windows on BOTH symbols, not a single test-split number.

- **Gap-traversal far targets (k>=2) for better RR**: SETTLED DEAD. Swept target
  = 1st..6th fib level out with tight wick stop. RR scales as hoped (0.7 -> 9.5)
  but expectancy fails walk-forward. BTC 1h k=6 positive in only 3/5 windows and
  entirely driven by one quarter (+44bps) + top-5 winners (half the total P&L);
  ETH 1h k=6 positive in 1/5 windows, NEGATIVE overall (-5.2bps). 5m negative at
  every k. Classic multiple-comparisons artifact (24 configs, 2 crossed zero,
  neither robust). The far-target RR story does not survive.
- **Gap-traversal as a Polymarket signal**: DEAD. It's a multi-bar touch-before-
  stop event, not a single-candle close. Leak-free next-candle test = 49% (z=-2.6).
- **Wick-rejection fade**: DEAD. 45-48% on hourly close across all geometries.
- **Zone-break bias state**: DEAD. 50.4% on hourly close.

## What actually survived everything (backtest) — and the LIVE update
- meanrev (RSI/MFI 5m -> Polymarket binary): 54-57% leak-free on close in backtest.
- gaptrav k=1: real ~68% touch rate, ~breakeven after costs in backtest.

**LIVE PAPER REFUTED THE BACKTEST (2026-06-15).** meanrev live = **45.9% over 109
Polymarket paper bets** (−$2080), the worst on the board → **DEMOTED**
live_candidate→paper. Strategies the audit dismissed did better; **far_targets**
(declared "settled-dead") was net-positive in live paper, and zone trading is
strongest on **higher timeframes** (gaptrav/far_targets best on 1h on equities).
Lesson, logged in the ledger: trust live paper over backtest ranking. The numbers
are in the DB / dashboard; the `lessons` table records why. This is the system
working — the paper gate meant zero real money was ever at risk.
