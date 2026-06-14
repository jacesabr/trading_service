# Polymarket 5-Minute BTC/ETH Prediction System — Research, Setup & Operating Plan
*Built June 2026. Companion code: indicator_battery.py, paper_trader.py, rules.json,
mr_5m.py, confluence_search.py, data.py*

## 1. What is established (16 months Binance 5m data, 3-way validated)

A single robust factor: **short-horizon mean reversion**, strongest on the
overbought side. Validated rules (held-out test, never touched during search):

| Rule | BTC test | ETH test | signals/day |
|---|---|---|---|
| rsi7 > 71 → Down | 55.0% (z=5.8) | 57.3% (z=8.3) | ~35 |
| bb_z > 2.0 → Down | ~56% | ~57% | ~18 |
| rsi14 > 60 → Down | 54.0% (z=6.5) | 55.7% (z=9.1) | ~70 |
| rsi7 < 33 → Up | ~53% | ~53% | ~35 |

ML ceiling on these features: test AUC 0.518 (BTC) / 0.528 (ETH); top-20%-
confidence accuracy 53–55%. Treat **57% as the realistic best case** for
indicator-only signals. Price expectancy is ~0 bps — the edge exists ONLY in
binary hit-rate terms, which is why Polymarket (binary payout) is the right
venue and spot/futures is not.

## 2. Market mechanics (verified)

- Resolution: Binance BTC/USDT (ETH/USDT) 5m candle; **close >= open → Up**
  (ties go Up). Same data this system is backtested and signaled on.
- Market discovery: slug = `{btc|eth}-updown-5m-{unix_epoch_of_window_start}`
  (epochs divisible by 300). Metadata via gamma-api `markets?slug=`, fallback
  scrape of `polymarket.com/event/{slug}` (both implemented in paper_trader).
- Order book: `clob.polymarket.com/book?token_id=` (public, no auth).
  Tick 0.01, min order 5 shares.
- **Fees (the decisive constraint):** dynamic TAKER fees on 5m/15m crypto
  markets, peaking ~3.15% at 50c and declining toward 0/100; introduced
  specifically to kill latency-arbitrage bots. **Maker (limit) orders are
  free.** Fee schedules have changed twice since Jan 2026 — re-verify monthly:
  https://docs.polymarket.com/developers/market-makers/maker-rebates-program

## 3. Known edges and traps (literature)

- The dominant historical edge was **latency arbitrage**: Polymarket odds lag
  Binance spot by seconds; bots bought confirmed momentum (one documented
  $313 → $414k/month, 98% win rate; ~$40M extracted Apr 2024–Apr 2025 per
  "Unravelling the Probabilistic Forest"). Fees now tax exactly this at the
  50c midpoint. Implication: pure taker strategies near 50c face a ~3% fee +
  1–2c spread + slippage hurdle ≈ **5–7% of stake**, vs our 8–14% gross edge
  at 54–57% accuracy. Margin is thin as a taker; healthy as a maker.
- A published live study of nearly this exact idea (5-min BTC, AI-screened
  momentum signals, Mar 2026) **lost money**: estimated 2–6% edge, eaten by
  1.56% fee + 2–4% slippage. Lessons it paid for: never market-order into
  thin books; signal must exist BEFORE the book moves; log everything.
- ~15–20% of 5m windows are decided in the final seconds — late entries at
  extreme prices are mostly fairly priced; the edge window is the first
  seconds after the boundary, before the new candle develops.
- Trap: backtested accuracy uses candle closes; live you act 1–2s after the
  boundary on a candle that closed milliseconds ago. Slippage between signal
  computation and fill is measurable only live → hence paper phase.

## 4. Operating protocol

**Phase 0 — verify (1 day).** On your machine: `python3 paper_trader.py
--probe`. Confirm Binance reachability, market discovery, book snapshots.
Read the live market rules text once; re-check fee docs.

**Phase 1 — paper (2–4 weeks).** Run the loop 24/7 (cheap VPS or spare
machine; restart-on-crash via systemd/pm2). Collects per signal: book state,
taker fill estimate with depth, hypothetical maker fill, fee, outcome, P&L
both ways. Target: 400+ resolved signals. `--report` gives hit rate,
calibration by entry price, and EV per bet taker vs maker.
- **Promotion criteria:** realized hit rate within 2pp of backtest AND
  net taker EV > +1.5%/bet, or maker-if-filled EV > +3%/bet with fill-rate
  evidence (book moved through your price).
- **Kill criteria:** hit rate < 52.5% after 400 signals, or average entry
  price for your side > your hit rate (market already prices the signal).

**Phase 2 — small live (only if promoted).** $5–20/signal, maker-first:
post limit at best-bid+1 tick on your side at the boundary, cancel at +60s
if unfilled. Maker = zero fee; the strategy's economics roughly double vs
taking. Track fill rate and adverse selection (are you only filled when
wrong?). 2 weeks minimum before any size increase. Hard daily loss limit.

**Phase 3 — improvement loop (ongoing).**
- Nightly: append new Binance candles, re-run indicator_battery on a rolling
  window; alert if live hit rate drifts >3pp below backtest (edge decay).
- The paper log itself becomes the alpha source: add **book features**
  (opening spread, depth imbalance, prior window's closing price) to the ML
  model — these are unavailable in candle backtests and are where real
  remaining edge likely lives.
- Walk-forward retrain monthly; never deploy a model that hasn't beaten the
  static rules on the most recent out-of-sample month.

## 5. AI usage (where it helps, where it doesn't)

Helps: nightly feature search/retraining; anomaly detection on the live log;
code maintenance. Doesn't: an LLM screening individual trades added nothing
in the published live study — the edge is statistical, not reasoning-based.
Calibrated gradient boosting on candle + book features is the right tool.

## 6. Selling it as a service — honest assessment

Sequence matters: **track record first** (3+ months of timestamped logs;
on-chain trades are independently verifiable — a genuine marketing asset),
then productize as a signal feed (Telegram bot / dashboard / API,
subscription). Realities to price in:
- **Capacity:** best-ask depth observed ~150–400 shares; the edge supports
  maybe low hundreds of $ per window. Subscribers trading the same signal
  move the book against each other — the service cannibalizes the edge.
  Capacity-capped pricing (limited seats) is the honest structure.
- **Decay:** this is a known, public effect now taxed by the venue; assume
  it shrinks. Sell the *system and ongoing research*, not a static rule.
- **Compliance:** signals-as-a-service for prediction markets sits in a gray
  zone that varies by jurisdiction (and Polymarket itself geo-restricts some
  users). Frame as educational/informational, no performance guarantees,
  document everything. Get real legal advice before charging money.
- Alternative monetization with better economics: publish the verified track
  record + research as content (the audience you already want for
  TradingView/consulting), and treat trading P&L as the product.

## 7. References
- Fee/rebate docs: docs.polymarket.com/developers/market-makers/maker-rebates-program
- Fee analysis: predictionhunt.com/blog/polymarket-fees-complete-guide
- Latency-arb history: financemagnates.com (dynamic fees article);
  "Unravelling the Probabilistic Forest" (Aug 2025)
- Failed live study (read this twice): medium.com/@gwrx2005 AI-augmented
  arbitrage, 5-minute BTC binary options (Mar 2026)
