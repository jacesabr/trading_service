# docs/TRADE_AUDIT.md — why our losses lost (audit boxes)

**Date:** 2026-06-17. **Trigger:** the `$NOW` weekly long was recorded a −209.7 bps
stop even though price never broke the author's trendline. Re-checking every
resolved trade showed **all six losses share one root cause**, not six bad calls.

> **The one bug.** We placed each idea as a **limit bracket at the author's
> entry**, regardless of where price actually was. When the entry sits on the
> *wrong side* of the live price, a limit order is **marketable** — it fills
> instantly at the current market, NOT at the level the author drew. The
> broker-held stop, sized against the *intended* entry, then ends up sitting on
> top of (or already through) the *actual* fill → an instant, meaningless stop.
> Sometimes the drift hurts (NOW −210, TSLA −152), once it helped by luck
> (GOOGL +27, mislabelled "stop"). It is never the trade the author published.

Marketable = a BUY limit placed **above** market, or a SELL limit placed **below**
market. Both fill now. A breakout entry (long above / short below price) therefore
can never "rest until the breakout" in a bracket — Alpaca rejects stop-entry
brackets ("bracket orders must be entry orders"), so the entry leg is limit/market
only. The fix is to **validate price location before placing** (below).

---

## Audit boxes — the 6 resolved losses

Legend: **drift** = (fill − intended entry); **stop room** = stop distance from the
*actual* fill. A healthy trade fills at/near the level (drift ≈ 0) with stop room
matched to the timeframe.

```
┌─ #39 NOW · LONG · 1w · Alpaca · basis=GENERATED ──────────── −209.7 bps ─┐
│ drawn:  entry 134  target 180  stop 100                                  │
│ live px at place ≈ 101  →  buy-limit @134 was ABOVE market = marketable  │
│ FILL @ 101.57 (−24% drift). stop 100 = 1.55% under the real fill.        │
│ A weekly swing with a 1.5% stop dies on noise → instant stop.            │
│ WRONG TWICE: (1) levels were auto-GENERATED, not chart-read. 134 is the  │
│   chart's RESISTANCE label (134.47), mis-used as the entry. (2) marketable│
│   fill + absolute tight stop.                                            │
│ Author's real structure: base ~80–100, resistance 134/140/146, rising    │
│   trendline. Invalidation = weekly close below the ~80 support zone.      │
│ VERDICT: re-enter LONG entry ~100 / stop ~78 (below 80 zone) / target 134.│
└──────────────────────────────────────────────────────────────────────────┘

┌─ #72 GOOGL · SHORT · 1h · Alpaca · basis=chart ──────────── +26.9 bps ⚠ ─┐
│ drawn:  entry 362  target 348  stop 369                                  │
│ live px ≈ 371  →  sell-limit @362 was BELOW market = marketable.         │
│ FILL @ 371.46 (+2.6% drift). stop 369 was now BELOW the fill → the       │
│   "stop" buy-stop triggered instantly and covered @370.46 = +27 bps.     │
│ Recorded "stop" but it was a tiny WIN — pure luck of drift direction.    │
│ The setup was already DEAD: price (371) was above the stop (369) before  │
│   we ever placed it. Should have been INVALIDATED, never traded.         │
│ VERDICT: do not re-enter — guard now invalidates "price already past     │
│   stop" at placement.                                                    │
└──────────────────────────────────────────────────────────────────────────┘

┌─ #30 AAPL · SHORT · 30m · Alpaca · basis=chart ──────────────── −1.3 bps ─┐
│ drawn: entry 290 target 282 stop 296.5.  live px ≈ 299 → sell-limit @290  │
│   below market = marketable. FILL @299.38 (+3.2% drift). Price already    │
│   above the stop 296.5 → setup invalidated before entry. Stopped at once. │
│ VERDICT: do not re-enter — same "already past stop" invalidation.         │
└──────────────────────────────────────────────────────────────────────────┘

┌─ #29 TSLA · LONG · 30m · Alpaca · basis=chart ──────────────── −151.5 bps ┐
│ drawn: entry 410 target 432 stop 398.  live px ≈ 404 → buy-limit @410      │
│   above market = marketable. FILL @404.02 (−1.5% drift). stop 398 = 1.49% │
│   under the fill (ok-ish for 30m). TSLA then fell to 398. HONEST-ish loss: │
│   a valid stop distance, real adverse move — but note we entered a        │
│   *pullback @404*, not the *breakout @410* the author wanted.             │
│ VERDICT: not a placement artifact. Re-enter only on a fresh chart read.   │
└──────────────────────────────────────────────────────────────────────────┘

┌─ #21 BTCUSD SHORT 2h · Bybit (−10.1) · #6 BTCUSDT SHORT 3h · Bybit (−62.9)┐
│ Both filled within ~1% of the intended entry and Bybit RE-ANCHORED the    │
│ TP/SL to the real fill (preserves the author's reward:risk). Real market  │
│ went against them by a small amount. These are HONEST losses — the system │
│ worked. No re-entry warranted.                                            │
└──────────────────────────────────────────────────────────────────────────┘
```

**Why Bybit losses are small and Alpaca losses are catastrophic:** `_resolve_bybit`
re-anchors the bracket to the *actual* fill (`tgt = fill + d·reward`, `stp = fill −
d·risk`), so RR survives a drifted fill. Alpaca submits the author's **absolute**
stop/target to the broker OCO, so a drifted fill leaves the stop stranded. The
Bybit pattern is the correct one; the new guard brings Alpaca up to the same safety.

---

## The fix (shipped in `ideas/execute.py`)

A **pre-placement validity gate** (`_entry_validity`) runs for every idea, using the
live price (Alpaca data API for equities, Binance book ticker for crypto):

1. **Resting limit** (long entry < px, short entry > px) → fills *at the level*. OK,
   place as-is. This is a disciplined pullback/limit entry.
2. **Marketable** (entry on the wrong side of px) → it will fill *now*, not at the
   level. Then:
   - target already reached at px → **invalidated** (setup played out).
   - price already at/through the stop → **invalidated** (setup breached — the
     GOOGL/AAPL case; this is exactly "price hasn't done what the idea needs").
   - **equities only:** stop room from the live price < the timeframe's floor
     (`MIN_STOP_FRAC`, e.g. 6% on 1w, 1% on 1h) → **invalidated** (would be
     noise-stopped — the NOW case). Bybit is exempt because it re-anchors.
   - otherwise the marketable fill is favorable (a better price, valid stop) → place.

Plus: **outcome is now labelled by P&L sign**, not by which OCO leg filled, so a
profitable exit can never again show as "stop" (the GOOGL bug).

What the guard would have done to the six: NOW, GOOGL, AAPL #30 → **invalidated**
(never placed). TSLA #29 → placed (valid). BTC #21/#6 → placed (valid, Bybit
re-anchored). i.e. it removes exactly the broken trades and keeps the honest ones.

See `tradingview_automation_run.md` → "Stop & target discipline" for the manual
chart-read rules that prevent mis-set levels upstream of this guard.
