"""gap_next5m.py — Leak-free Polymarket test (vectorized).

Signal fires at bar t (move is past). Bet the FIRST full 5m candle opening
strictly after t's close, IF the traversal trade is still live at that
boundary (not stopped, not target-touched between entry and the boundary).
Bet that candle's close direction. The bet candle is entirely future => no leak.
"""
import sys
import numpy as np
import pandas as pd
from gap_traversal import rolling_zone_bands, find_signals, load

WIN_MS = 5 * 60 * 1000


def build_5m(df1):
    ts = df1["ts"].to_numpy()
    win = (ts // WIN_MS) * WIN_MS
    g = pd.DataFrame({"o": df1["open"].to_numpy(), "h": df1["high"].to_numpy(),
                      "l": df1["low"].to_numpy(), "c": df1["close"].to_numpy(),
                      "win": win}).groupby("win")
    five = pd.DataFrame({"win": list(g.groups.keys()),
                         "o": g["o"].first().values, "c": g["c"].last().values,
                         "n": g.size().values})
    return five[five["n"] == 5].reset_index(drop=True)


def run(sym, sig_tf):
    df = load(sym, sig_tf)
    df1 = load(sym, "1m")
    ltf = df if sig_tf == "1m" else load(sym, "5m")
    lb = 120 if sig_tf == "1m" else 100
    every = 10 if sig_tf == "1m" else 24
    levels = rolling_zone_bands(df, ltf, lb, 0.70, every)
    signals = find_signals(df, levels)
    five = build_5m(df1)
    fwins = five["win"].to_numpy()
    o5 = five["o"].to_numpy(); c5 = five["c"].to_numpy()

    ts = df["ts"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy(); n = len(df)

    rows = []
    for s in signals:
        t = s["t"]; d = s["dir"]; tgt = s["target"]; stp = s["stop"]
        te = t + 1
        if te >= n:
            continue
        first_bound = ((ts[t] // WIN_MS) + 1) * WIN_MS
        # bars from entry up to (but not including) the first bar at/after bound
        j = np.searchsorted(ts, first_bound, "left")   # first bar index >= bound
        if j <= te:
            seg_h = np.empty(0); seg_l = np.empty(0); seg_c = np.empty(0)
        else:
            seg_h = h[te:j]; seg_l = l[te:j]; seg_c = c[te:j]
        # live if no stop-close and no target-touch in [te, j)
        if d > 0:
            stopped = np.any(seg_c <= stp)
            hitt = np.any(seg_h >= tgt)
        else:
            stopped = np.any(seg_c >= stp)
            hitt = np.any(seg_l <= tgt)
        if stopped or hitt:
            continue
        wi = np.searchsorted(fwins, first_bound)
        if wi >= len(fwins) or fwins[wi] != first_bound:
            continue
        out = np.sign(c5[wi] - o5[wi]); out = 1 if out == 0 else out
        rows.append((first_bound, d, int(d == out), o5[wi], c5[wi]))

    pm = pd.DataFrame(rows, columns=["win", "dir", "won", "o", "c"]).drop_duplicates("win")
    n_ = len(pm)
    print(f"[{sym} signal={sig_tf}] leak-free bets: n={n_}")
    if n_ < 50:
        print("  too few\n"); return
    up = (np.sign(pm.c - pm.o) > 0)
    base = max(up.mean(), 1 - up.mean())
    hit = pm.won.mean(); z = (hit - 0.5) / np.sqrt(0.25 / n_)
    split = pm.win.quantile(0.7); te_ = pm[pm.win >= split]
    zt = (te_.won.mean() - 0.5) / np.sqrt(0.25 / len(te_)) if len(te_) > 30 else float("nan")
    print(f"  hit={hit:.1%} baseline={base:.1%} edge={hit-base:+.1%} z={z:+.1f}")
    print(f"  longs={pm[pm.dir>0].won.mean():.1%} (n={(pm.dir>0).sum()}) "
          f"shorts={pm[pm.dir<0].won.mean():.1%} (n={(pm.dir<0).sum()})")
    print(f"  TEST(30%): n={len(te_)} hit={te_.won.mean():.1%} z={zt:+.1f}\n")


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    for tf in ("5m", "1m"):
        run(sym, tf)


if __name__ == "__main__":
    main()
