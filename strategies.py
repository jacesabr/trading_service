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
from zone_breaks import run_engine

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


# ---------- Strategy C: gaptrav_tight (TODO promoted to paper) -------------
def _wick_buffer(df, frac=0.0005):
    """Tight-stop levels just beyond the just-closed candle's wick extremes."""
    h = float(df["high"].iloc[-1]); l = float(df["low"].iloc[-1])
    return l * (1 - frac), h * (1 + frac)          # (long_stop, short_stop)


def gaptrav_tight_open(df, zones_bands):
    """gaptrav entry/target, but stop sits just beyond the entry candle's wick
    instead of the origin zone border. CLAUDE.md TODO: ~68% win, breakeven ~55%,
    ~-1..-5 bps — the most promising untested-live config. PAPER."""
    tr = gaptrav_open(df, zones_bands)
    if not tr:
        return None
    long_stop, short_stop = _wick_buffer(df)
    tr = dict(tr)
    tr["stop"] = long_stop if tr["direction"] > 0 else short_stop
    tr["rule"] = "gap_close_tight"
    return tr


# ---------- Strategy D: meanrev_confluence (TODO promoted to paper) --------
def meanrev_confluence_open(df5, zones_bands):
    """A gaptrav trade taken ONLY when meanrev agrees on direction. Tests
    whether confluence of the two beats either parent. CLAUDE.md TODO. PAPER."""
    tr = gaptrav_open(df5, zones_bands)
    if not tr:
        return None
    side, _ = meanrev_signal(df5)
    if side is None:
        return None
    if (1 if side == "Up" else -1) != tr["direction"]:
        return None
    tr = dict(tr); tr["rule"] = "gap_x_meanrev"
    return tr


# ---------- Strategy E: far_targets (settled-negative, paper control) ------
def far_targets_open(df, zones_bands, k=3):
    """gaptrav with the target k zones out and a tight wick stop. Research
    SETTLED-NEGATIVE (fails walk-forward); wired live only to confirm decay."""
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    price = c[-1]
    gap_i = None
    for i in range(len(zones_bands) - 1):
        if zones_bands[i][1] < price < zones_bands[i+1][0]:
            gap_i = i; break
    if gap_i is None:
        return None
    long_stop, short_stop = _wick_buffer(df)
    if c[-1] >= o[-1]:
        ti = min(gap_i + k, len(zones_bands) - 1)
        return dict(direction=1, target=zones_bands[ti][0], stop=long_stop,
                    gap=gap_i, rule=f"gap_far_k{k}")
    ti = max(gap_i + 1 - k, 0)
    return dict(direction=-1, target=zones_bands[ti][1], stop=short_stop,
                gap=gap_i, rule=f"gap_far_k{k}")


# ---------- Strategy F: wick_fade (settled-negative, paper control) --------
def wick_fade_signal(df5):
    """Fade the just-closed 5m candle's dominant wick (rejection): a long upper
    wick -> predict Down next bar; long lower wick -> Up. Predicts the NEXT 5m
    close direction. Research SETTLED-NEGATIVE (45-48% on 1h); paper control."""
    o = float(df5["open"].iloc[-1]); h = float(df5["high"].iloc[-1])
    l = float(df5["low"].iloc[-1]);  c = float(df5["close"].iloc[-1])
    rng = h - l
    if rng <= 0:
        return None, None
    upper = (h - max(o, c)) / rng
    lower = (min(o, c) - l) / rng
    body = abs(c - o) / rng
    if body > 0.35:
        return None, None
    if upper >= 0.55:
        return -1, "upper_wick_reject"
    if lower >= 0.55:
        return 1, "lower_wick_reject"
    return None, None


# ---------- Strategy G: zone_break_bias (settled-negative, paper control) --
def zone_break_bias_signal(df5, df1, lookback=100):
    """Directional bias from the causal Zone-Breaks engine on 5m: a pending
    breakout -> predict Up next bar, pending breakdown -> Down. Predicts the
    NEXT 5m close. Research SETTLED-NEGATIVE (50.4% on 1h); paper control."""
    if len(df5) < lookback + 40 or len(df1) < 200:
        return None, None
    try:
        _, up_s, dn_s, *_ = run_engine(df5.reset_index(drop=True),
                                       df1.reset_index(drop=True),
                                       lookback=lookback, value_area_share=0.70)
    except Exception:
        return None, None
    d = int(up_s[-1]) - int(dn_s[-1])           # state at last closed bar
    if d == 0:
        return None, None
    return (1 if d > 0 else -1), "zone_pending_bias"


# ---------- Shared registry: status is honest, used by runner + dashboard --
# kind: "binary" = predicts next-bar close direction (spot bps; + Polymarket
#                  bet for BTC/ETH on meanrev). "bracket" = SL/TP traversal.
# Registry is manifest-only now (2026-06-21). The legacy human-facing dicts were
# removed in the API-testability + dedup cleanup: every surviving strategy is a
# self-contained child manifest (rlab/registry/*.json) wired to a real broker API
# at 5m. The signal FUNCTIONS below stay (the live batteries import them); only the
# legacy display registry is retired. New strategies = a manifest + an impl fn.
STRATEGIES = {}


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
