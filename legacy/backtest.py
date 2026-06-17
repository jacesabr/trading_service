"""
backtest.py — Accuracy harness for the Zone Breaks engine.

Target: at each chart bar's close, the engine may emit a prediction for the
NEXT bar's direction (close vs open). No information from the predicted bar
is used to form the signal.

Signals tested
  event   : a break_up / break_dn confirmed at bar t predicts bar t+1
  valid   : a valid_up / valid_dn confirmed at bar t predicts bar t+1
  invalid : contrarian — invalid_up at t predicts t+1 DOWN, invalid_dn -> UP
  state   : while a breakout is pending validation at bar t's close,
            predict t+1 up (mirror for breakdowns; conflict -> no signal)

Metrics per signal
  n           number of predicted bars (dojis excluded from hits, counted in n)
  hit_rate    share of predicted bars where sign(close-open) matched
  baseline    hit rate of the best constant guess (always-up or always-down,
              whichever is more frequent) over the SAME bars
  edge        hit_rate - baseline (the only number that means anything)
  expectancy  mean of pred * (close-open)/open in basis points
              (direction-weighted average bar return; fees not included)
"""
import sys
import numpy as np
import pandas as pd
from zone_breaks import run_engine


def load(sym, tf):
    return pd.read_csv(f"{sym}_{tf}.csv")


def build_predictions(chart, events, up_state, dn_state):
    n = len(chart)
    preds = {k: np.zeros(n, dtype=np.int8)
             for k in ("event", "valid", "invalid", "state")}
    for e in events:
        t = e.t + 1                       # prediction is for the NEXT bar
        if t >= n:
            continue
        if e.kind == "break_up":   preds["event"][t] += 1
        if e.kind == "break_dn":   preds["event"][t] -= 1
        if e.kind == "valid_up":   preds["valid"][t] += 1
        if e.kind == "valid_dn":   preds["valid"][t] -= 1
        if e.kind == "invalid_up": preds["invalid"][t] -= 1   # contrarian
        if e.kind == "invalid_dn": preds["invalid"][t] += 1
    st = up_state.astype(np.int16) - dn_state.astype(np.int16)
    preds["state"][1:] = np.sign(st[:-1]).astype(np.int8)
    for k in preds:
        preds[k] = np.sign(preds[k]).astype(np.int8)          # conflicts -> 0
    return preds


def score(pred, chart, mask=None):
    o = chart["open"].to_numpy()
    c = chart["close"].to_numpy()
    outcome = np.sign(c - o)
    sel = pred != 0
    if mask is not None:
        sel &= mask
    n = int(sel.sum())
    if n == 0:
        return dict(n=0, hit=np.nan, base=np.nan, edge=np.nan, exp_bps=np.nan)
    hits = float((pred[sel] == outcome[sel]).mean())
    up_share = float((outcome[sel] > 0).mean())
    base = max(up_share, (outcome[sel] < 0).mean())
    exp_bps = float(np.mean(pred[sel] * (c[sel] - o[sel]) / o[sel]) * 1e4)
    return dict(n=n, hit=hits, base=base, edge=hits - base, exp_bps=exp_bps)


def evaluate(chart, ltf, lookback, va_share, mask=None, verbose=False):
    events, up_s, dn_s, _, _ = run_engine(chart, ltf, lookback, va_share)
    preds = build_predictions(chart, events, up_s, dn_s)
    rows = {}
    for name, p in preds.items():
        rows[name] = score(p, chart, mask)
    if verbose:
        from collections import Counter
        print("  events:", dict(Counter(e.kind for e in events)))
    return rows


def fmt(rows):
    out = []
    for name, r in rows.items():
        if r["n"] == 0:
            out.append(f"  {name:<8} n=0")
        else:
            out.append(f"  {name:<8} n={r['n']:<5} hit={r['hit']:.1%} "
                       f"baseline={r['base']:.1%} edge={r['edge']:+.1%} "
                       f"expectancy={r['exp_bps']:+.1f} bps/bar")
    return "\n".join(out)


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    chart, ltf = load(sym, "1h"), load(sym, "5m")
    n = len(chart)
    split = int(n * 0.7)
    train_mask = np.zeros(n, bool); train_mask[:split] = True
    test_mask = ~train_mask
    t0 = pd.to_datetime(chart.ts.iloc[0], unit="ms")
    ts = pd.to_datetime(chart.ts.iloc[split], unit="ms")
    t1 = pd.to_datetime(chart.ts.iloc[-1], unit="ms")
    print(f"{sym} 1h, {n} bars  |  train {t0:%Y-%m-%d} -> {ts:%Y-%m-%d}  "
          f"|  test -> {t1:%Y-%m-%d}\n")

    print("=== Default parameters (lookback=100, VA=70%) — full period ===")
    rows = evaluate(chart, ltf, 100, 0.70, verbose=True)
    print(fmt(rows), "\n")

    print("=== Hyperparameter sweep on TRAIN only ===")
    grid = [(lb, va) for lb in (50, 100, 150, 200) for va in (0.60, 0.70, 0.80)]
    results = []
    for lb, va in grid:
        rows = evaluate(chart, ltf, lb, va, mask=train_mask)
        for name, r in rows.items():
            if r["n"] >= 30:
                results.append((name, lb, va, r))
        best_line = max(rows.items(), key=lambda kv: (kv[1]["edge"] or -9))
        print(f"lb={lb:<3} va={int(va*100)}%")
        print(fmt(rows))
    if results:
        results.sort(key=lambda x: x[3]["edge"], reverse=True)
        name, lb, va, r = results[0]
        print(f"\nBest on train: signal={name} lb={lb} va={int(va*100)}% "
              f"edge={r['edge']:+.1%} (n={r['n']})")
        print("=== Same config on held-out TEST ===")
        rows = evaluate(chart, ltf, lb, va, mask=test_mask)
        print(fmt(rows))
