# CLAUDE.md — Project guide for Claude Code

Polymarket/forex signal research + live tracking system. Read this first, then
SYSTEM.md for architecture and STRATEGY.md for the validated edges and the
error audit.

## What this project is
Backtests, validates, and live-tracks short-horizon BTC/ETH trading signals.
Two markets: Polymarket 5m binary (close direction) and forex/CFD (SL/TP).
Everything resolves on Binance candles — the same venue Polymarket settles on.

## The cardinal rule of this codebase: NO LOOKAHEAD
Every signal must be computable from data available BEFORE the outcome bar.
This has bitten us repeatedly. Three guards, never remove them:
1. Features lag by one bar in backtests (`features(df, lag=True)`); live uses
   `lag=False` to read the just-closed bar. They must represent the SAME thing.
2. For Polymarket: the bet candle must open AFTER the signal fires. Predicting
   a candle whose formation overlaps the signal is leakage (we measured a fake
   63% that was really 50% on the un-formed remainder — see gap_next5m.py).
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
- strategies.py — both signals, ONE import. Backtests + live call these. Single
  source of truth; if you change signal logic, change it HERE only.
- db.py — SQLite (signals/bets/trades). record_*/resolve_*/stats/open_positions.
- runner.py — live loop (5m boundary -> signal -> DB -> resolve).
- dashboard_db.py — monitor on :8050, reads DB.
- backtest entry points: gap_traversal.py, indicator_battery.py, mr_5m.py,
  gap_next5m.py (the leak test), confluence_search.py.
- executor.py (Polymarket CLOB) / forex_oanda.py (OANDA practice) — live order
  layers, both with confirm_live=True gates and size caps. Don't loosen rails.

## Data
`python3 data.py SYMBOL CHARTTF LTF START_YM END_YM` -> SYMBOL_TF.csv (Binance
dumps, no API key). Zones need a lower TF than the chart (5m zones need 1m).

## Conventions
- Don't add a dependency without noting it in SYSTEM.md deploy steps.
- bps = basis points of price return; "expectancy" is always net-of-nothing
  gross unless a fee/spread is named. Polymarket EV is per-$1-staked.
- Sizes: Polymarket min 5 shares (not $5); dollar cost = 5 * price. Configurable
  in runner.py SIZE_USD. Forex paper sizes in forex_oanda.
- Never commit tracker.db or *.csv data dumps.

## Safe local commands (no live trading, no keys needed)
```bash
python3 db.py                         # init/verify DB
python3 strategies.py                 # signal smoke test on stored data
python3 gap_traversal.py BTCUSDT 1h 5m # a backtest
python3 gap_next5m.py BTCUSDT         # the leak test (run for any new idea)
python3 runner.py --probe            # connectivity check (needs Binance reach)
```

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

## What actually survived everything
- meanrev (RSI/MFI 5m -> Polymarket binary): 54-57% leak-free on close. The one
  live-worthy edge. Thin; needs live confirmation of entry prices vs hit rate.
- gaptrav k=1 (nearest target): real ~68% touch rate but ~breakeven after costs.
  Kept as a PAPER experiment only — watch for live divergence, do not fund.
