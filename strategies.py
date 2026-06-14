"""
strategies.py — The two validated strategies behind one importable interface.
Single source of truth: backtests AND live loggers call these same functions,
so live can never drift from what was validated.

  meanrev_signal(df5)        -> ('Up'|'Down'|None, rule) for the JUST-CLOSED 5m
                                bar, predicting the NEXT 5m close (Polymarket).
                                LIVE-CANDIDATE: leak-free, 54-57% on test data.

  gaptrav_open(df, zones)    -> dict|None describing a new gap-traversal trade
                                (dir/entry/stop/target) at the just-closed bar.
                                PAPER-ONLY: real ~58-61% touch rate but ~0 net
                                expectancy after costs; tracked, not traded live.

Validated rule set for meanrev is in rules.json (RSI/MFI thresholds, 3-way
validated). gaptrav uses the original-script fib zone bands + travel gaps.
"""
import json
import numpy as np
import pandas as pd
from indicator_battery import features
from gap_traversal import rolling_zone_bands, find_signals, ZONES

# ---------- Strategy A: RSI/MFI mean-reversion (Polymarket binary) ----------
_RULES = None


def _rules():
    global _RULES
    if _RULES is None:
        _RULES = json.load(open("rules.json"))
    return _RULES


def meanrev_signal(df5):
    """df5: recent 5m OHLCV (>=200 bars). Returns (side, rule) for predicting
    the NEXT 5m candle's close, or (None, None). Uses indicators at the LAST
    CLOSED bar (lag=False) — the leak-free construction."""
    feats = features(df5, lag=False)
    for r in _rules():
        ok = True
        for cond in r["conditions"]:
            x = feats[cond["feature"]][-1]
            if np.isnan(x):
                ok = False; break
            if cond["op"] == ">" and not x > cond["thr"]:
                ok = False; break
            if cond["op"] == "<" and not x < cond["thr"]:
                ok = False; break
        if ok:
            return ("Up" if r["dir"] > 0 else "Down"), r["name"]
    return None, None


# ---------- Strategy B: gap-traversal (forex SL/TP, paper-only) ----------
def gaptrav_open(df, zones_bands):
    """df: recent OHLCV; zones_bands: list of (bottom,top) fib bands active now.
    If the just-closed bar closed inside a travel gap, return a trade dict for
    next-bar entry, else None. Mirrors gap_traversal.find_signals for one bar."""
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    price = c[-1]
    # locate gap
    gap_i = None
    for i in range(len(zones_bands) - 1):
        if zones_bands[i][1] < price < zones_bands[i+1][0]:
            gap_i = i; break
    if gap_i is None:
        return None
    up = c[-1] >= o[-1]
    if up:
        return dict(direction=1, target=zones_bands[gap_i+1][0],
                    stop=zones_bands[gap_i][1], gap=gap_i)
    return dict(direction=-1, target=zones_bands[gap_i][1],
                stop=zones_bands[gap_i+1][0], gap=gap_i)


def current_zone_bands(chart_df, ltf_df, lookback=100, va=0.70):
    """Compute the fib zone bands from the most recent window (for live use)."""
    levels = rolling_zone_bands(chart_df, ltf_df, lookback, va,
                                every=len(chart_df))  # compute once at the end
    return levels[-1]


if __name__ == "__main__":
    from gap_traversal import load
    df5 = load("BTCUSDT", "5m").tail(300).reset_index(drop=True)
    side, rule = meanrev_signal(df5)
    print(f"meanrev on latest 5m tail: {side} ({rule})")
    bands = current_zone_bands(df5, load("BTCUSDT", "1m").tail(1500).reset_index(drop=True))
    if bands:
        tr = gaptrav_open(df5, bands)
        print(f"gaptrav on latest tail: {tr if tr else 'no gap-close'}")
        print(f"  zone bands: {[(round(b),round(t)) for b,t in bands][:3]}...")
