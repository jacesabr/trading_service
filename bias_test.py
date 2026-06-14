"""
bias_test.py — Regime test for Zone Breaks.

Hypothesis: while the system is LONG-biased, the share of bars closing above
their open exceeds the unconditional share (mirror for SHORT bias).

Bias is set at a bar's CLOSE and applies to all following bars until changed.
Four regime definitions tested:

  pending    : long only while a breakout awaits validation (short windows)
  from_break : long from break_up; cleared to neutral by invalid_up or a
               zone recompute; flipped by break_dn (mirror for shorts)
  break_flip : like from_break, but an invalidation flips bias to the
               OPPOSITE side (failed breakout = bearish) instead of neutral
  from_valid : long from valid_up; cleared by invalid_up / recompute;
               flipped by valid_dn

Per regime and side we report: bars n, up-share, unconditional up-share over
the whole sample, z-score of the difference (two-sided null: regime bars are
drawn from the unconditional distribution), and mean signed bar return in bps.

Usage: python3 bias_test.py BTCUSDT [lookback] [va_share]
"""
import sys
import numpy as np
import pandas as pd
from zone_breaks import run_engine


def load(sym, tf):
    return pd.read_csv(f"{sym}_{tf}.csv")


def build_bias(n, events, up_state, dn_state, mode):
    bias = np.zeros(n, dtype=np.int8)
    if mode == "pending":
        st = up_state.astype(int) - dn_state.astype(int)
        bias[1:] = np.sign(st[:-1])
        return bias
    cur = 0
    ev_iter = iter(sorted(events, key=lambda e: e.t))
    e = next(ev_iter, None)
    for t in range(n - 1):
        while e is not None and e.t == t:
            k = e.kind
            if mode in ("from_break", "break_flip"):
                if k == "break_up":   cur = 1
                elif k == "break_dn": cur = -1
                elif k == "invalid_up":
                    cur = -1 if mode == "break_flip" else 0
                elif k == "invalid_dn":
                    cur = 1 if mode == "break_flip" else 0
                elif k == "stale":    cur = 0
            elif mode == "from_valid":
                if k == "valid_up":     cur = 1
                elif k == "valid_dn":   cur = -1
                elif k in ("invalid_up", "invalid_dn", "stale"):
                    cur = 0
            e = next(ev_iter, None)
        bias[t + 1] = cur          # state at close of t governs bar t+1
    return bias


def regime_report(bias, chart, label, mask=None):
    o = chart["open"].to_numpy(); c = chart["close"].to_numpy()
    decided = c != o                       # exclude dojis everywhere
    up = c > o
    base_mask = decided if mask is None else (decided & mask)
    p0 = float(up[base_mask].mean())       # unconditional up-share
    rows = [f"{label}  (unconditional up-share = {p0:.1%}, "
            f"n={int(base_mask.sum())})"]
    for side, name in ((1, "LONG bias "), (-1, "SHORT bias")):
        sel = base_mask & (bias == side)
        n = int(sel.sum())
        if n < 30:
            rows.append(f"  {name}: n={n} (too few)")
            continue
        p = float(up[sel].mean())
        # null: regime bars drawn from unconditional distribution
        z = (p - p0) / np.sqrt(p0 * (1 - p0) / n)
        favorable = p - p0 if side == 1 else p0 - p
        exp = float(np.mean(side * (c[sel] - o[sel]) / o[sel]) * 1e4)
        rows.append(f"  {name}: n={n:<5} up-share={p:.1%}  "
                    f"edge={favorable:+.1%}  z={z * side:+.2f}  "
                    f"signed-exp={exp:+.1f} bps/bar")
    return "\n".join(rows)


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    va = float(sys.argv[3]) if len(sys.argv) > 3 else 0.70
    chart, ltf = load(sym, "1h"), load(sym, "5m")
    n = len(chart)
    events, up_s, dn_s, _, _ = run_engine(chart, ltf, lookback, va)
    split = int(n * 0.7)
    test_mask = np.zeros(n, bool); test_mask[split:] = True

    print(f"{sym} 1h  lookback={lookback} va={int(va*100)}%  ({n} bars)\n")
    for mode in ("pending", "from_break", "break_flip", "from_valid"):
        bias = build_bias(n, events, up_s, dn_s, mode)
        print(regime_report(bias, chart, f"[{mode}] full period"))
        print(regime_report(bias, chart, f"[{mode}] test only ", test_mask))
        print()
