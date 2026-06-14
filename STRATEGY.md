# The Strategy, In Plain Language — and the Error Audit

## What the strategy is (one paragraph)

Bitcoin and Ethereum 5-minute candles slightly **mean-revert**: after a
short burst up (overbought), the next candle closes down a bit more often
than not, and after a burst down, it closes up a bit more often. The tilt is
small — 54–58% instead of 50% — and carries almost no money in price terms,
because the reverting candles are small. But Polymarket's 5-minute Up/Down
markets pay on **direction only**, so a directional tilt is the entire game
there. The system watches each just-closed Binance 5m candle; when momentum
indicators say "stretched" (e.g. RSI(7) above 71 AND price more than 1.65
standard deviations above its 20-bar average), it predicts the next candle
closes the other way and paper-buys that side on Polymarket.

## The three deployed rules (verified on never-touched test data, last 4 months)

| Rule (all conditions must hold) | Predicts | BTC test | ETH test | signals/day |
|---|---|---|---|---|
| RSI7 > 71 and bb_z > 1.65 | Down | 55.4% (z=+5.3) | 57.6% (z=+7.4) | ~20 |
| RSI14 > 60 and bb_z > 2.0 | Down | 55.0% (z=+3.9) | 58.2% (z=+6.5) | ~13 |
| RSI7 < 33 and bb_z < -1.65 | Up | 54.1% (z=+4.7) | 56.1% (z=+7.0) | ~27 |

Why it can be profitable on Polymarket and nowhere else: a 56% true
probability bought at 50–52c returns ~8–12% per dollar gross. The hurdles
are the dynamic taker fee (~3% at 50c — avoidable by posting maker limit
orders, which are free) and the entry price itself (if the market already
quotes 55–56c when the rule fires, the edge is priced — this is exactly what
the paper phase measures and the calibration chart shows).

## The audit — every error class checked

1. **Lookahead bias (backtest)** — CLEAN. All indicators use trailing
   windows; the feature array is shifted one bar so features[t] is computed
   strictly from bars ≤ t−1, and the outcome is bar t. Verified by index
   inspection.
2. **Lookahead/staleness (live path)** — **BUG FOUND AND FIXED** in this
   audit. paper_trader reused the backtest's lagged feature array and read
   its last element, which is the indicator of the *second-to-last* candle —
   live signals would have been one bar staler than the backtest, silently
   degrading live results vs expectation. Fixed: live mode computes
   unlagged features (lag=False) at the just-closed candle. Demonstrated
   with a concrete example (RSI7 56.3 vs the stale 62.2 it would have used).
3. **Train/test contamination** — CLEAN. Thresholds derived from BTC train
   data only; selection on validation; the test slice was read once.
   ETH never participated in threshold selection at all.
4. **Multiple-comparisons** — CONTROLLED. 296 rules scored; chance max-z
   ≈ 3.4 documented; deployed rules clear it on both symbols independently.
5. **Data integrity** — CLEAN. Uniform 5m timestamp spacing, no gaps,
   139,680 bars/symbol, Binance official dumps — the same venue Polymarket
   resolves on.
6. **Tie handling** — MINOR HONEST BIAS, documented. Backtest excludes
   dojis (close == open, 0.2–0.3% of bars); Polymarket resolves ties as
   "Up". Real-world effect: Down predictions lose ~0.1–0.3pp vs backtest;
   Up predictions gain the same. Negligible but real; the live log captures
   it automatically.
7. **Fee model** — APPROXIMATION, flagged. Peak ~3.15% at 50c with linear
   decay is reconstructed from reporting, not the exact formula (which has
   changed twice in 2026). Maker-mode economics don't depend on it; taker
   EV numbers do. Re-verify monthly against the official docs.
8. **Backtest-vs-live timing gap** — STRUCTURAL, measurable only live.
   Backtest assumes acting exactly at the candle boundary; live there are
   1–3 seconds of latency during which books move. This is the main reason
   the paper phase exists and why its fills come from real order-book
   snapshots, not assumptions.

## On "let the LLM decide trades in real time"

The honest evidence is against the LLM as the primary decider: a non-linear
learner over all 19 features (gradient boosting — strictly more numeric
precision than an LLM reading the same numbers) capped at test AUC 0.52–0.53,
and the one published live study of LLM-screened 5-minute signals lost
money while adding latency. The edge is a small statistical tilt, not a
reasoning problem.

But the claim deserves a test, not an argument — so the system now runs
**shadow voting** (set LLM_SHADOW=1): on every signal, the LLM receives the
same indicators and live book, votes TRADE or SKIP, and the vote is logged
but never gates. After 200+ votes, `--report` compares EV of LLM-approved
vs LLM-rejected signals. If approved signals significantly outperform, the
gate has earned promotion with data behind it. If not, the question is
settled the same way. Where an LLM genuinely can add non-linear value
meanwhile: the daily analyst run (pattern-finding across the whole log) and
event awareness (e.g. skip windows around scheduled FOMC/CPI releases — an
experiment the analyst can propose and the log can verify).

## Far-target RR sweep — settled negative (added after testing)
Tested targeting the 1st through 6th fib level beyond entry with a tight wick
stop, to chase a 1:5 RR. RR scaled correctly (avgRR 0.7->9.5 as targets moved
out) but expectancy failed the robustness gate: BTC 1h k=6 was positive in only
3/5 walk-forward windows and driven by a single quarter plus the top-5 winners;
ETH 1h k=6 was negative overall (-5.2bps, positive in 1/5 windows); all 5m
configs negative. This is a multiple-comparisons artifact, not an edge. The
nearest-target (k=1) ~breakeven characterization stands. Far targets manufacture
RR at the cost of trading pure variance. Not deployed.
