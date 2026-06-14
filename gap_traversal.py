"""
gap_traversal.py — The actual strategy: gap-close -> next-zone traversal.

Zones are the original script's 8 fib PAIRS (bands of price points). Between
consecutive bands are TRAVEL GAPS. The rule:

  * SIGNAL: a candle CLOSES inside a travel gap (not in any zone band).
  * DIRECTION: toward the nearer adjacent zone? No — toward the next zone in
    the direction the gap was entered. We define it by which border the close
    sits above/below: a close in a gap predicts travel to the NEXT zone in the
    direction of the gap's far side. We test BOTH a long bias (target = upper
    zone of the gap) and the symmetric short (target = lower zone), labelled by
    which the price is heading toward; the engine takes the side implied by the
    candle that entered the gap (close > open -> up, else down).
  * WIN: intrabar TOUCH of the target zone's near border.
  * LOSS: a single CLOSE back beyond the origin zone's border the move left
    from (top border if going up, bottom border if going down).
  * Random-walk baseline reported (target_dist vs stop_dist first-passage).

Pullback-entry variant (entry_mode='pullback'): instead of entering at the
gap-close, wait for price to dip back toward the origin zone (a wick that
re-approaches within `pull_frac` of the gap depth) and enter there — same
target and stop. Compares whether the cheaper entry improves expectancy.

Usage: python3 gap_traversal.py BTCUSDT 1h 5m
"""
import sys
import numpy as np
import pandas as pd
from zone_breaks import compute_profile, ltf_for_chart_tf

# original script zones as (bottom%, top%) of the VAL->VAH range
ZONES = [(0.0, 23.6), (38.2, 50.0), (61.8, 78.6), (100.0, 127.2),
         (138.2, 150.0), (161.8, 176.4), (200.0, 224.0), (261.8, 300.0)]


def load(sym, tf):
    return pd.read_csv(f"{sym}_{tf}.csv")


def rolling_zone_bands(chart, ltf, lookback, va, every):
    """levels[t] = list of (bottom_price, top_price) zone bands active at t."""
    h = chart["high"].to_numpy(); l = chart["low"].to_numpy()
    c = chart["close"].to_numpy(); v = chart["volume"].to_numpy()
    ts = chart["ts"].to_numpy(); n = len(chart)
    ms = int(np.median(np.diff(ts)))
    lts = ltf["ts"].to_numpy()
    lhi = ltf["high"].to_numpy(); llo = ltf["low"].to_numpy(); lvo = ltf["volume"].to_numpy()
    bs = np.searchsorted(lts, ts, "left"); be = np.searchsorted(lts, ts + ms, "left")
    ratio = max((ms // 1000) // (ltf_for_chart_tf(ms // 1000) * 60), 1)
    min_ltf = 0.75 * lookback * ratio
    out = [None] * n; cur = None
    for t in range(lookback, n):
        if (t - lookback) % every == 0:
            w0 = t - lookback + 1; s, e = bs[w0], be[t]
            if e - s >= min_ltf:
                prof = compute_profile(h[w0:t+1], l[w0:t+1], v[w0:t+1],
                                       lhi[s:e], llo[s:e], lvo[s:e], va)
                if prof:
                    val, vah = prof; rng = vah - val
                    cur = [(val + rng*b/100, val + rng*tp/100) for b, tp in ZONES]
        out[t] = cur
    return out


def classify(price, bands):
    """Return ('zone', i) if price in band i, else ('gap', i) where i is the
    index of the zone BELOW the gap (gap between band i and i+1), or None."""
    for i, (b, tp) in enumerate(bands):
        if b <= price <= tp:
            return ("zone", i)
    for i in range(len(bands) - 1):
        if bands[i][1] < price < bands[i+1][0]:
            return ("gap", i)
    return (None, None)


def find_signals(chart, levels):
    o = chart["open"].to_numpy(); c = chart["close"].to_numpy()
    n = len(chart); sig = []
    for t in range(n - 1):
        bands = levels[t]
        if bands is None:
            continue
        kind, i = classify(c[t], bands)
        if kind != "gap":
            continue
        up = c[t] >= o[t]                       # direction the gap was entered
        if up:
            target = bands[i+1][0]              # near border of zone above
            stop_border = bands[i][1]           # top border of origin zone (below)
            d = 1
        else:
            target = bands[i][1]                # near border of zone below
            stop_border = bands[i+1][0]         # bottom border of origin zone (above)
            d = -1
        sig.append(dict(t=t, dir=d, gap=i, target=target, stop=stop_border,
                        close=c[t]))
    return sig


def simulate(chart, signals, entry_mode="close", pull_frac=0.5, max_hold=72):
    o = chart["open"].to_numpy(); h = chart["high"].to_numpy()
    l = chart["low"].to_numpy(); c = chart["close"].to_numpy(); n = len(chart)
    rows = []
    for s in signals:
        d = s["dir"]; tgt = s["target"]; stp = s["stop"]
        te0 = s["t"] + 1
        if te0 >= n:
            continue
        if entry_mode == "close":
            te = te0; entry = o[te]
        else:
            # wait up to max_hold for a pullback that re-approaches the origin
            gap_depth = abs(s["close"] - stp)
            trigger = stp + d * pull_frac * gap_depth   # partway back toward origin
            te = None
            for u in range(te0, min(te0 + max_hold, n)):
                # stop still respected while waiting
                if (c[u] <= stp) if d > 0 else (c[u] >= stp):
                    break
                touched_pull = (l[u] <= trigger) if d > 0 else (h[u] >= trigger)
                if touched_pull:
                    te = u + 1 if u + 1 < n else None
                    break
            if te is None:
                continue
            entry = o[te]
        # validity: target still ahead, stop still behind at entry
        if (tgt - entry) * d <= 0:
            continue
        outcome = "timeout"; exit_p = c[min(te+max_hold, n-1)]; texit = min(te+max_hold, n-1)
        for u in range(te, min(te + max_hold, n)):
            stop_hit = (c[u] <= stp) if d > 0 else (c[u] >= stp)   # CLOSE beyond border
            tgt_hit = (h[u] >= tgt) if d > 0 else (l[u] <= tgt)    # intrabar TOUCH
            if stop_hit:
                outcome = "stop"; exit_p = c[u]; texit = u; break
            if tgt_hit:
                outcome = "target"; exit_p = tgt; texit = u; break
        ret = d * (exit_p - entry) / entry
        rw = abs(entry - stp) / (abs(entry - stp) + abs(tgt - entry))
        rows.append(dict(t=s["t"], dir=d, t_entry=te, entry=entry, target=tgt,
                         stop=stp, exit=exit_p, outcome=outcome, ret=ret,
                         rw_base=rw, won=int(outcome == "target"),
                         pm_won=int(np.sign(c[te] - o[te]) == d), bars=texit-te))
    return pd.DataFrame(rows)


def report(df, label):
    if len(df) < 20:
        return f"  {label:<16} n={len(df)} (thin)"
    res = df[df.outcome.isin(["target", "stop"])]
    wr = res.won.mean() if len(res) else np.nan
    rw = df.rw_base.mean()
    exp = df.ret.mean() * 1e4
    return (f"  {label:<16} n={len(df):<4} win={wr:.1%} rw_base={rw:.1%} "
            f"edge={wr-rw:+.1%} exp={exp:+.0f}bps pm={df.pm_won.mean():.0%} "
            f"hold={df.bars.mean():.0f}")


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    cf = sys.argv[2] if len(sys.argv) > 2 else "1h"
    lf = sys.argv[3] if len(sys.argv) > 3 else "5m"
    chart, ltf = load(sym, cf), load(sym, lf)
    n = len(chart); split = int(n * 0.7)
    levels = rolling_zone_bands(chart, ltf, 100, 0.70, 24)
    signals = find_signals(chart, levels)
    print(f"{sym} {cf}: {n} bars, {len(signals)} gap-close signals\n")

    for mode in ("close", "pullback"):
        df = simulate(chart, signals, entry_mode=mode)
        if df.empty:
            print(f"[{mode}] no trades\n"); continue
        print(f"[entry = {mode}]")
        print(report(df, "all"))
        print(report(df[df.dir>0], "long"))
        print(report(df[df.dir<0], "short"))
        print(report(df[df.t>=split], "TEST"))
        print(f"  outcomes: {df.outcome.value_counts().to_dict()}\n")

    print("win = base rule hit rate; edge = win - random-walk baseline.")
    print("pm = entry-bar close direction (Polymarket event).")


if __name__ == "__main__":
    main()
