"""clv_momentum -- agent-authored strategy signal.

Hypothesis: a 5m bar that closes in the extreme of its own range on above-median
volume reflects intrabar order-flow imbalance (aggressive buyers/sellers winning
the close) that persists into the next bar. Close-location-value (CLV) =
(close-low)/(high-low) in [0,1]; near 1 = closed on the high (buying), near 0 =
closed on the low (selling).

Contract: pure + as_of-indexed. Reads ONLY the just-closed bar (last row) and
earlier. Returns (side, rule) with side in {'Up','Down',None}. The harness scores
the prediction on the NEXT independent bar's open->close, so there is no lookahead.
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
        return ("Up", "clv_high_volup")
    if clv <= lo_thr:
        return ("Down", "clv_low_volup")
    return (None, None)
