"""
mr_5m.py — Test the overbought/oversold mean-reversion effect on 5-minute
bars (the Polymarket '5 Min' Up/Down event: close vs open of the 5m candle).

Signal at close of bar t-1, prediction for bar t. Train = first 60%,
quarterly stability shown for the full period. Dojis (close == open)
excluded from hit rates; their share is reported since Polymarket resolves
ties Up.

Usage: python3 mr_5m.py
"""
import numpy as np
import pandas as pd
from confluence_search import load, rsi, mfi

RULES = []
for thr in (60, 65, 70, 75, 80):
    RULES.append((f"rsi>{thr} -> DOWN", ("rsi", ">", thr), -1))
for thr in (40, 35, 30, 25, 20):
    RULES.append((f"rsi<{thr} -> UP  ", ("rsi", "<", thr), +1))
for thr in (80, 85, 90):
    RULES.append((f"mfi>{thr} -> DOWN", ("mfi", ">", thr), -1))
for thr in (20, 15, 10):
    RULES.append((f"mfi<{thr} -> UP  ", ("mfi", "<", thr), +1))


def run(sym):
    df = load(sym, "5m")
    o, h, l, c, v = [df[k].to_numpy() for k in
                     ("open", "high", "low", "close", "volume")]
    n = len(df)
    outcome = np.sign(c - o)
    doji_share = float((outcome == 0).mean())
    ts = pd.to_datetime(df["ts"], unit="ms")
    lagged = {"rsi": np.concatenate([[np.nan], rsi(c)[:-1]]),
              "mfi": np.concatenate([[np.nan], mfi(h, l, c, v)[:-1]])}
    split = int(n * 0.6)
    is_test = np.zeros(n, bool); is_test[split:] = True
    qtr = ts.dt.to_period("Q").to_numpy()

    print(f"== {sym} 5m  ({n} bars, doji share {doji_share:.1%}) ==")
    print(f"{'rule':<18}{'n/day':>6}{'train%':>8}{'test%':>7}{'test z':>8}"
          f"{'exp bps':>8}  quarterly")
    for name, (ind, op, thr), d in RULES:
        x = lagged[ind]
        cond = (x > thr) if op == ">" else (x < thr)
        sel = cond & (outcome != 0)
        if sel.sum() < 500:
            continue
        hit_tr = float((outcome[sel & ~is_test] == d).mean())
        te = sel & is_test
        hit_te = float((outcome[te] == d).mean())
        z_te = (hit_te - 0.5) / np.sqrt(0.25 / te.sum())
        exp = float(np.mean(d * (c[sel] - o[sel]) / o[sel]) * 1e4)
        per_q = " ".join(
            f"{(outcome[sel & (qtr == q)] == d).mean():.0%}"
            for q in pd.unique(qtr) if (sel & (qtr == q)).sum() >= 200)
        n_day = sel.sum() / (n / 288)
        print(f"{name:<18}{n_day:>6.1f}{hit_tr:>8.1%}{hit_te:>7.1%}"
              f"{z_te:>+8.1f}{exp:>+8.1f}  {per_q}")
    print()


if __name__ == "__main__":
    for s in ("BTCUSDT", "ETHUSDT"):
        run(s)
