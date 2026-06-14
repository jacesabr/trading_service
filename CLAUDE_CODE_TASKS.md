# Claude Code — ready-to-run task prompts

Paste any of these to Claude Code in this repo. Each is scoped, testable, and
respects the no-lookahead rules in CLAUDE.md.

## 1. Add the tight-wick-stop strategy (highest priority)
"Add `gaptrav_tight` to strategies.py: same gap-close signal and next-zone
target as gaptrav, but stop placed just beyond the ENTRY BAR's wick extreme
(low for longs, high for shorts) minus/plus 0.15*ATR(14), close-based.
Add a backtest entry mirroring gap_traversal.py with TRAIN/VAL/TEST and the
random-walk baseline. Then wire it into runner.py as a third DB strategy tag
and show it in dashboard_db.py. Before shipping, run the gap_next5m-style leak
check adapted for touch-targets and report whether TEST expectancy is positive
after a 1bps spread. Do not loosen any no-lookahead guard."

## 2. Add meanrev as a confluence filter
"Add `meanrev_confluence` as a separate strategy: when gaptrav (or gaptrav_tight)
fires, call meanrev_signal on the same 5m data; take the trade only when meanrev
does NOT contradict the trade direction (overbought->down filters out longs,
etc.). Log it under its own DB strategy tag so we can compare filtered vs
unfiltered hit rate and expectancy. Add a backtest that reports all three:
gaptrav alone, meanrev alone, and the confluence — on TEST, both symbols."

## 3. Configurable micro-bet sizing for real Polymarket paper-testing
"In runner.py and executor.py, replace the fixed SIZE_USD with a config that
supports a SHARE-count minimum (Polymarket orderMinSize=5 shares). Add a mode
that caps dollar risk by only taking bets when the entry price keeps 5 shares
under a configurable $ ceiling (e.g. $2). Log the actual share count and dollar
cost per bet. Keep paper mode (book-snapshot fills) as default; live mode behind
the existing confirm_live gate."

## 4. Promotion-gate automation
"Add gate.py: reads the DB and evaluates the PLAN.md promotion criteria per
strategy (>=400 resolved, live hit within 2pp of backtest, avg entry < hit
rate for meanrev; positive net expectancy for the forex ones). Print a clear
PROMOTE/HOLD/KILL verdict per strategy. Add it to the daily analyst run and
surface the verdict on the dashboard."

## 5. Public read-only feed + Telegram push
"Add a read-only public view to dashboard_db.py (no controls, just the live
track record + calibration) on a separate route. Add telegram_push.py that, on
each new resolved bet/trade in the DB, posts a summary to a Telegram channel via
bot API (token in env). This is the seed of the information-service feed; keep
it strictly read-only and add a disclaimer line (educational, no guarantees)."

## 6. Monthly re-validation automation
"Add revalidate.py: pulls the latest month of Binance data, re-runs
indicator_battery on a rolling window, and flags any rule in rules.json whose
validation z-score has dropped below 1.5. Output a short report; do NOT auto-
edit rules.json — propose changes for human approval per CLAUDE.md."
