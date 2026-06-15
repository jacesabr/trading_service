"""clv_fade -- agent-authored strategy signal.

Hypothesis (mean reversion): a 5m bar that closes in the extreme of its own range
on above-median volume has OVERSHOT -- aggressive flow exhausts and the next bar
reverts. So we FADE the close location: closed-on-the-high -> predict Down,
closed-on-the-low -> predict Up. This is the exact inverse of clv_momentum, whose
continuation hypothesis was rejected (robustly negative, 5/5 walk-forward windows
on both BTCUSDT and ETHUSDT). The edge here is that same structure, read the
correct way round.

Contract: pure + as_of-indexed. Reads ONLY the just-closed bar (last row) and
earlier. Returns (side, rule). The harness scores on the NEXT independent bar.
"""
import numpy as np


def signal(df, params=None):
    p = params or {}
    hi_thr = float(p.get("hi_thr", 0.80))     # CLV at/above -> closed on the high
    lo_thr = float(p.get("lo_thr", 0.20))     # CLV at/below -> closed on the low
    vol_lb = int(p.get("vol_lb", 48))         # volume-median lookback (bars)
    if len(df) < vol_lb + 1:
        return (None, None)

    last = df.iloc[-1]
    hi, lo, cl = float(last["high"]), float(last["low"]), float(last["close"])
    rng = hi - lo
    if rng <= 0:
        return (None, None)                    # doji / zero-range bar: no info
    clv = (cl - lo) / rng

    vol = df["volume"].to_numpy()[-vol_lb:]
    if not float(last["volume"]) > float(np.median(vol)):
        return (None, None)                    # need conviction (above-median vol)

    if clv >= hi_thr:
        return ("Down", "fade_high_close")     # closed on the high -> fade down
    if clv <= lo_thr:
        return ("Up", "fade_low_close")        # closed on the low -> fade up
    return (None, None)
