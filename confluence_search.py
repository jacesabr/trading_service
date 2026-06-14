"""
confluence_search.py — Exhaustive, honest search for ANY iteration of the
Zone Breaks system (plus RSI / Money Flow confluences) that predicts whether
the next 1h bar closes above or below its open.

Design:
  * Features are computed at bar t-1's CLOSE; the prediction is for bar t.
  * Zone states generated for several (lookback, va, qualified) configs.
  * Rule grid: {zone bias source} x {RSI condition} x {MFI condition}
    x {trade with bias / fade bias}, plus indicator-only rules.
  * TRAIN = first 60%, TEST = last 40%. Rules are ranked on train z-score;
    only the top rules are then read on test. With N rules tested, the max
    train z expected from pure chance is ~sqrt(2*ln(N)) (~3 for N=500) —
    printed for context so train results can't oversell themselves.
  * ML check: gradient boosting on the pooled feature matrix. If no feature
    combination carries signal, test AUC will sit at ~0.5.

Usage: python3 confluence_search.py BTCUSDT
"""
import sys
import numpy as np
import pandas as pd
from zone_breaks import run_engine
from bias_test import build_bias

TRAIN_FRAC = 0.60
MIN_TRAIN_N = 100


def load(sym, tf):
    return pd.read_csv(f"{sym}_{tf}.csv")


def rsi(close, n=14):
    d = np.diff(close, prepend=close[0])
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50).to_numpy()


def mfi(h, l, c, v, n=14):
    tp = (h + l + c) / 3
    raw = tp * v
    d = np.diff(tp, prepend=tp[0])
    pos = pd.Series(np.where(d > 0, raw, 0.0)).rolling(n).sum()
    neg = pd.Series(np.where(d < 0, raw, 0.0)).rolling(n).sum()
    r = pos / neg.replace(0, np.nan)
    return (100 - 100 / (1 + r)).fillna(50).to_numpy()


def score(pred, outcome, mask):
    sel = (pred != 0) & mask & (outcome != 0)
    n = int(sel.sum())
    if n == 0:
        return n, np.nan, np.nan
    p = float((pred[sel] == outcome[sel]).mean())
    z = (p - 0.5) / np.sqrt(0.25 / n)
    return n, p, z


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    chart, ltf = load(sym, "1h"), load(sym, "5m")
    o = chart["open"].to_numpy(); h = chart["high"].to_numpy()
    l = chart["low"].to_numpy(); c = chart["close"].to_numpy()
    v = chart["volume"].to_numpy()
    n = len(chart)
    outcome = np.sign(c - o)
    split = int(n * TRAIN_FRAC)
    train = np.zeros(n, bool); train[:split] = True
    test = ~train

    # ---- indicator features at t-1, aligned to predict bar t -------------
    def lag(x): return np.concatenate([[np.nan], x[:-1]])
    RSI = lag(rsi(c)); MFI = lag(mfi(h, l, c, v))
    prev_dir = lag(np.sign(c - o))
    prev_ret = lag((c - o) / o)
    rsi_conds = {"any": np.ones(n, bool),
                 "rsi<30": RSI < 30, "rsi<40": RSI < 40,
                 "rsi>60": RSI > 60, "rsi>70": RSI > 70,
                 "40<rsi<60": (RSI > 40) & (RSI < 60)}
    mfi_conds = {"any": np.ones(n, bool),
                 "mfi<20": MFI < 20, "mfi<30": MFI < 30,
                 "mfi>70": MFI > 70, "mfi>80": MFI > 80}

    # ---- zone-system states across hyperparameter configs ---------------
    zone_cfgs = [(50, 0.60, False), (100, 0.70, False), (150, 0.70, False),
                 (100, 0.70, True), (150, 0.80, True), (200, 0.70, True)]
    biases = {}
    for lb, va, q in zone_cfgs:
        ev, up_s, dn_s, *_ = run_engine(chart, ltf, lb, va, qualify=q)
        tag = f"lb{lb}/va{int(va*100)}{'/Q' if q else ''}"
        biases[f"pending {tag}"] = build_bias(n, ev, up_s, dn_s, "pending")
        biases[f"frombreak {tag}"] = build_bias(n, ev, up_s, dn_s, "from_break")
    print(f"zone configs done ({len(biases)} bias series)\n")

    # ---- rule grid -------------------------------------------------------
    rules = []
    for bname, b in biases.items():
        for rname, rc in rsi_conds.items():
            for mname, mc in mfi_conds.items():
                cond = rc & mc
                base = np.where(cond, b, 0).astype(np.int8)
                rules.append((f"{bname} & {rname} & {mname} [with]", base))
                if rname != "any" or mname != "any":
                    rules.append((f"{bname} & {rname} & {mname} [fade]", -base))
    # indicator-only rules (no zone state)
    for rname, rc in rsi_conds.items():
        if rname == "any":
            continue
        d = 1 if "<" in rname else -1            # oversold -> up, overbought -> down
        rules.append((f"{rname} only [revert]", np.where(rc, d, 0).astype(np.int8)))
        rules.append((f"{rname} only [momo]", np.where(rc, -d, 0).astype(np.int8)))
    for mname, mc in mfi_conds.items():
        if mname == "any":
            continue
        d = 1 if "<" in mname else -1
        rules.append((f"{mname} only [revert]", np.where(mc, d, 0).astype(np.int8)))
        rules.append((f"{mname} only [momo]", np.where(mc, -d, 0).astype(np.int8)))

    results = []
    for name, pred in rules:
        n_tr, p_tr, z_tr = score(pred, outcome, train)
        if n_tr >= MIN_TRAIN_N:
            results.append((name, pred, n_tr, p_tr, z_tr))
    results.sort(key=lambda r: r[4], reverse=True)
    exp_max_z = np.sqrt(2 * np.log(max(len(results), 2)))
    print(f"rules evaluated: {len(results)}  "
          f"(max train z expected by pure chance ≈ {exp_max_z:.1f})\n")
    print(f"{'rule':<58} {'train n':>7} {'train%':>7} {'z':>6} | "
          f"{'test n':>6} {'test%':>7} {'z':>6}")
    for name, pred, n_tr, p_tr, z_tr in results[:8]:
        n_te, p_te, z_te = score(pred, outcome, test)
        print(f"{name:<58} {n_tr:>7} {p_tr:>7.1%} {z_tr:>+6.2f} | "
              f"{n_te:>6} {p_te if p_te==p_te else 0:>7.1%} {z_te if z_te==z_te else 0:>+6.2f}")

    # ---- ML: can ANY combination of features predict the close? ---------
    print("\nML check (HistGradientBoosting on pooled features):")
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    feats = [RSI, MFI, prev_dir, prev_ret]
    names = ["rsi", "mfi", "prev_dir", "prev_ret"]
    for bname, b in biases.items():
        feats.append(b.astype(float)); names.append(bname)
    X = np.column_stack(feats)
    y = (outcome > 0).astype(int)
    ok = ~np.isnan(X).any(axis=1) & (outcome != 0)
    Xtr, ytr = X[ok & train], y[ok & train]
    Xte, yte = X[ok & test], y[ok & test]
    clf = HistGradientBoostingClassifier(max_iter=300, max_depth=4,
                                         learning_rate=0.05, random_state=0)
    clf.fit(Xtr, ytr)
    auc_tr = roc_auc_score(ytr, clf.predict_proba(Xtr)[:, 1])
    auc_te = roc_auc_score(yte, clf.predict_proba(Xte)[:, 1])
    acc_te = (clf.predict(Xte) == yte).mean()
    print(f"  train AUC={auc_tr:.3f}   TEST AUC={auc_te:.3f}   "
          f"TEST accuracy={acc_te:.1%}  (0.500 AUC / ~50% acc = no signal)")
    # confident-only slice: top-20% most confident test predictions
    proba = clf.predict_proba(Xte)[:, 1]
    conf = np.abs(proba - 0.5)
    thr = np.quantile(conf, 0.8)
    m = conf >= thr
    acc_conf = ((proba[m] > 0.5).astype(int) == yte[m]).mean()
    print(f"  most-confident 20% of test bars: n={int(m.sum())} "
          f"accuracy={acc_conf:.1%}")
