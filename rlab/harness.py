"""rlab.harness — the leak-safe research engine (leak test / backtest /
walk-forward / grid). Lean rebuild 2026-06-21.

THE CARDINAL RULE (NO LOOKAHEAD) is enforced HERE, not by signal authors. Every
prediction is made as_of bar t and scored on bar t+1's independent close, so an
edge measured here is one that was computable BEFORE the outcome existed.

Two leak-free evaluation paths, both predicting the next independent bar:
  * rule-vectorized — feature-threshold rule sets (signal.rules / rules_file):
    evaluate over features(df, lag=False) in one pass. feats[t] is the indicator
    AT bar t; the rule at t predicts the close of t+1.
  * windowed-callable — universal path for arbitrary signal fns: slide a bounded
    trailing window ending at t, call fn(window[, params]), predict t+1. The fn
    must NEVER read beyond the last row of its window.

Scope: binary / directional next-bar edge — that is what proves or refutes a
signal on history. bracket (SL/TP) execution is NOT backtested here; it runs on
the live broker APIs (crypto_paper -> Bybit demo, equity_paper -> Alpaca paper)
where fills are broker-confirmed. Research mandate: timeframes <=5m.

Data: reads data/{SYMBOL}_{tf}.csv (fetch with data.py).
"""
import importlib
import math
import os

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO, "data")
DEFAULT_WINDOW = 300
MIN_N = 50            # too-few-samples guard


class HarnessPending(NotImplementedError):
    pass


# --------------------------------------------------------------------------- #
# data + signal resolution
# --------------------------------------------------------------------------- #
def _load_chart(symbol, tf):
    path = os.path.join(DATA_DIR, f"{symbol}_{tf}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"data/{symbol}_{tf}.csv missing — fetch with: "
            f"python data.py {symbol} {tf} <start_ym> <end_ym>")
    return pd.read_csv(path)


def _resolve_fn(manifest):
    sig = manifest["signal"]
    mod = importlib.import_module(sig["module"])
    return getattr(mod, sig["fn"]), dict(sig.get("params", {}))


def _rules_for(manifest):
    sig = manifest["signal"]
    if "rules" in sig:
        return sig["rules"]
    rf = sig.get("params", {}).get("rules_file")
    if rf:
        import json
        return json.load(open(os.path.join(REPO, rf)))
    return None


# --------------------------------------------------------------------------- #
# prediction (leak-free): pred[t] in {+1,-1,0}; 0 = no signal. Predicts t+1.
# --------------------------------------------------------------------------- #
def _predict_rules(df, rules):
    from indicator_battery import features
    feats = features(df, lag=False)              # value AT each bar t
    n = len(df)
    pred = np.zeros(n, dtype=int)
    for t in range(n):
        for r in rules:
            ok = True
            for cond in r["conditions"]:
                x = feats[cond["feature"]][t]
                if np.isnan(x) or \
                   (cond["op"] == ">" and not x > cond["thr"]) or \
                   (cond["op"] == "<" and not x < cond["thr"]):
                    ok = False
                    break
            if ok:
                pred[t] = 1 if r["dir"] > 0 else -1
                break
    return pred


def _norm_dir(side):
    if side is None:
        return 0
    if isinstance(side, str):
        return {"up": 1, "down": -1}.get(side.lower(), 0)
    return int(np.sign(side))


def _predict_callable(df, fn, params, window, lo=0, hi=None):
    """Predictions for [lo:hi) only, so a TEST/VAL-only eval doesn't rescan all
    history per combo."""
    n = len(df)
    hi = n if hi is None else min(hi, n)
    pred = np.zeros(n, dtype=int)
    start = max(window - 1, lo)
    for t in range(start, hi):
        w = df.iloc[t - window + 1:t + 1]
        try:
            out = fn(w, params)
        except TypeError:
            out = fn(w)
        side = out[0] if isinstance(out, tuple) else out
        pred[t] = _norm_dir(side)
    return pred


def _predictions(df, manifest, params=None, lo=0, hi=None):
    if manifest.get("kind") == "bracket":
        raise HarnessPending(
            "bracket (SL/TP) edges are not backtested here — they run on the "
            "live broker APIs (Bybit demo / Alpaca paper) for broker-confirmed "
            "fills. Research directional/binary signals here.")
    rules = _rules_for(manifest)
    if rules is not None and not params:
        return _predict_rules(df, rules)
    fn, p = _resolve_fn(manifest)
    if params:
        p.update(params)
    window = int(manifest["signal"].get("window", DEFAULT_WINDOW))
    return _predict_callable(df, fn, p, window, lo=lo, hi=hi)


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def _score(df, pred, lo=0, hi=None):
    """Score predictions on [lo:hi). pred[t] predicts sign(c[t+1]-o[t+1]).
    Returns dict(n, hit, baseline, edge, z) over signalled, non-doji bars."""
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    hi = (n - 1) if hi is None else min(hi, n - 1)
    out_next = np.sign(c[1:] - o[1:])            # outcome of bar t+1 at index t
    idx = np.arange(lo, hi)
    sel = idx[(pred[lo:hi] != 0) & (out_next[lo:hi] != 0)]
    m = len(sel)
    if m == 0:
        return dict(n=0, hit=None, baseline=None, edge=None, z=None)
    won = (pred[sel] == out_next[sel])
    hit = float(won.mean())
    up = float((out_next[sel] > 0).mean())
    baseline = max(up, 1 - up)
    z = (hit - 0.5) / math.sqrt(0.25 / m)
    return dict(n=m, hit=hit, baseline=baseline, edge=hit - baseline, z=z)


def _splits(n):
    return int(n * 0.50), int(n * 0.75)          # train / val / test boundaries


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def _symbols(manifest):
    return manifest.get("data", {}).get("symbols", []) or []


def _tf(manifest):
    return manifest.get("data", {}).get("timeframe", "5m")


def leaktest(manifest, **kw):
    """Leak-free held-out hit on the TEST slice, pooled across symbols.
    PASS = enough samples AND TEST z >= 2 (a real, non-50% tilt)."""
    tf = _tf(manifest)
    per = {}
    pooled_n = pooled_hit_w = 0
    for sym in _symbols(manifest):
        try:
            df = _load_chart(sym, tf)
        except FileNotFoundError:
            continue
        _, i2 = _splits(len(df))
        pred = _predictions(df, manifest, lo=i2)
        s = _score(df, pred, lo=i2)
        per[sym] = s
        if s["n"]:
            pooled_n += s["n"]; pooled_hit_w += s["hit"] * s["n"]
    if pooled_n < MIN_N:
        return dict(passed=False, reason=f"too few TEST signals ({pooled_n})",
                    n=pooled_n, per_symbol=per)
    hit = pooled_hit_w / pooled_n
    z = (hit - 0.5) / math.sqrt(0.25 / pooled_n)
    return dict(passed=bool(z >= 2.0), n=pooled_n, hit=hit, z=z, per_symbol=per,
                reason=("TEST z>=2, leak-free tilt" if z >= 2 else
                        f"TEST z={z:+.1f} < 2 — no edge"))


def backtest(manifest, **kw):
    """Per-symbol TRAIN / VAL / TEST hit, edge-vs-baseline, z."""
    tf = _tf(manifest); out = {}
    for sym in _symbols(manifest):
        try:
            df = _load_chart(sym, tf)
        except FileNotFoundError:
            out[sym] = {"error": "no data"}; continue
        pred = _predictions(df, manifest)
        i1, i2 = _splits(len(df))
        out[sym] = {"train": _score(df, pred, 0, i1),
                    "val": _score(df, pred, i1, i2),
                    "test": _score(df, pred, i2)}
    passed = all(
        (v.get("test") or {}).get("z") is not None and v["test"]["z"] >= 2.0
        for v in out.values() if "error" not in v) and bool(out)
    return dict(passed=passed, per_symbol=out)


def walkforward(manifest, n_windows=5, embargo=12, **kw):
    """Rolling out-of-sample windows with an embargo (purge) before each test
    block. Robust = z>0 in >=ceil(0.8*k) windows on EVERY symbol (>=4/5)."""
    tf = _tf(manifest); out = {}; need = math.ceil(0.8 * n_windows)
    all_ok = bool(_symbols(manifest))
    for sym in _symbols(manifest):
        try:
            df = _load_chart(sym, tf)
        except FileNotFoundError:
            out[sym] = {"error": "no data"}; all_ok = False; continue
        pred = _predictions(df, manifest)
        n = len(df); step = n // n_windows; wins = []
        for k in range(n_windows):
            lo = k * step + (embargo if k else 0)
            hi = (k + 1) * step if k < n_windows - 1 else n - 1
            wins.append(_score(df, pred, lo, hi))
        pos = sum(1 for w in wins if w["z"] is not None and w["z"] > 0)
        out[sym] = {"windows": wins, "positive": pos, "of": n_windows,
                    "robust": pos >= need}
        all_ok = all_ok and pos >= need
    return dict(passed=all_ok, need=need, per_symbol=out)


def gridsearch(manifest, **kw):
    """Sweep signal.param_grid; score each combo on VALIDATION (pooled). Reports
    the chance-max-z to beat so a swept winner isn't mistaken for an edge."""
    grid = manifest["signal"].get("param_grid", {})
    if not grid:
        return dict(passed=False, reason="no param_grid declared", combos=[])
    keys = list(grid)
    combos = [{}]
    for k in keys:
        combos = [dict(c, **{k: v}) for c in combos for v in grid[k]]
    tf = _tf(manifest); results = []
    for params in combos:
        pooled_n = 0; pooled_hit_w = 0.0
        for sym in _symbols(manifest):
            try:
                df = _load_chart(sym, tf)
            except FileNotFoundError:
                continue
            i1, i2 = _splits(len(df))
            win = int(manifest["signal"].get("window", DEFAULT_WINDOW))
            pred = _predictions(df, manifest, params=params,
                                lo=max(0, i1 - win), hi=i2)
            s = _score(df, pred, i1, i2)
            if s["n"]:
                pooled_n += s["n"]; pooled_hit_w += s["hit"] * s["n"]
        if pooled_n >= MIN_N:
            hit = pooled_hit_w / pooled_n
            z = (hit - 0.5) / math.sqrt(0.25 / pooled_n)
            results.append({"params": params, "n": pooled_n, "hit": hit, "z": z})
    results.sort(key=lambda r: -r["z"])
    chance_max_z = round(math.sqrt(2 * math.log(max(len(combos), 2))), 2)
    return dict(passed=bool(results and results[0]["z"] > chance_max_z),
                chance_max_z=chance_max_z, n_combos=len(combos),
                combos=results[:15])
