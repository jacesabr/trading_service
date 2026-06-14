"""
indicator_battery.py — Systematic search for 5m close-vs-open predictors.

Discipline:
  * Split: TRAIN 50% / VALIDATION 25% / TEST 25% (chronological).
  * Single rules ranked on TRAIN, top-k re-scored on VALIDATION;
    pairwise combos built only from validation survivors;
    TEST is read once, at the end, for the final shortlist.
  * A rule only counts as robust if it is positive on BOTH symbols' validation.
  * ML: gradient boosting on all features; honest number is TEST AUC.

Usage: python3 indicator_battery.py
"""
import numpy as np
import pandas as pd
from confluence_search import load, rsi, mfi

SYMS = ("BTCUSDT", "ETHUSDT")
TOP_SINGLE = 25
TOP_COMBO = 12
QUANTS = (0.02, 0.05, 0.10, 0.20, 0.80, 0.90, 0.95, 0.98)


def sma(x, n): return pd.Series(x).rolling(n).mean().to_numpy()


def features(df, lag=True):
    o, h, l, c, v = [df[k].to_numpy() for k in
                     ("open", "high", "low", "close", "volume")]
    n = len(df)
    f = {}
    f["rsi7"], f["rsi14"], f["rsi21"] = rsi(c, 7), rsi(c, 14), rsi(c, 21)
    f["mfi14"] = mfi(h, l, c, v, 14)
    ll = pd.Series(l).rolling(14).min().to_numpy()
    hh = pd.Series(h).rolling(14).max().to_numpy()
    f["stoch14"] = 100 * (c - ll) / np.where(hh - ll > 0, hh - ll, np.nan)
    tp = (h + l + c) / 3
    f["cci20"] = (tp - sma(tp, 20)) / (0.015 * pd.Series(tp).rolling(20)
                  .apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
                  .to_numpy())
    sd = pd.Series(c).rolling(20).std().to_numpy()
    f["bb_z"] = (c - sma(c, 20)) / np.where(sd > 0, sd, np.nan)
    for k in (1, 3, 6, 12):
        rk = (c - np.roll(c, k)) / np.roll(c, k); rk[:k] = 0
        f[f"ret{k}"] = rk * 1e4
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)),
                                      abs(l - np.roll(c, 1))))
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    f["atr_ret"] = (c - np.roll(c, 1)) / np.where(atr > 0, atr, np.nan)
    vs = sma(v, 20)
    f["vol_ratio"] = v / np.where(vs > 0, vs, np.nan)
    f["signed_vol"] = np.sign(c - o) * f["vol_ratio"]
    d = np.sign(c - o)
    streak = np.zeros(n)
    for i in range(1, n):
        streak[i] = streak[i - 1] + d[i] if (d[i] == np.sign(streak[i - 1])
                    or streak[i - 1] == 0) else d[i]
    f["streak"] = streak
    rng = h - l
    f["range_pos"] = (c - l) / np.where(rng > 0, rng, np.nan)
    f["body_frac"] = (c - o) / np.where(rng > 0, rng, np.nan)
    pv = (tp * v)
    f["vwap_dev"] = (c / (pd.Series(pv).rolling(144).sum()
                     / pd.Series(v).rolling(144).sum()).to_numpy() - 1) * 1e4
    obv = np.cumsum(np.sign(np.diff(c, prepend=c[0])) * v)
    f["obv_slope"] = (obv - np.roll(obv, 12)) / np.where(vs > 0, vs * 12, np.nan)
    if not lag:          # live mode: value AT the last closed candle
        return f
    # lag everything by 1 bar: signal at close of t-1 predicts bar t
    return {k: np.concatenate([[np.nan], x[:-1]]) for k, x in f.items()}


def hit(pred_dir, cond, outcome, mask):
    sel = cond & mask & (outcome != 0)
    n = int(sel.sum())
    if n < 200:
        return n, np.nan, np.nan
    p = float((outcome[sel] == pred_dir).mean())
    return n, p, (p - 0.5) / np.sqrt(0.25 / n)


if __name__ == "__main__":
    data = {}
    for s in SYMS:
        df = load(s, "5m")
        out = np.sign(df["close"].to_numpy() - df["open"].to_numpy())
        n = len(df)
        i1, i2 = int(n * 0.50), int(n * 0.75)
        tr = np.zeros(n, bool); tr[:i1] = True
        va = np.zeros(n, bool); va[i1:i2] = True
        te = np.zeros(n, bool); te[i2:] = True
        data[s] = dict(f=features(df), out=out, tr=tr, va=va, te=te, n=n)
    feat_names = list(data[SYMS[0]]["f"].keys())
    print(f"features: {len(feat_names)} | bars/symbol: {data[SYMS[0]]['n']}")

    # ---- stage 1: single rules ranked on train --------------------------
    singles = []
    ref = data[SYMS[0]]
    for name in feat_names:
        x = ref["f"][name]
        qs = np.nanquantile(x[ref["tr"]], QUANTS)
        for q, thr in zip(QUANTS, qs):
            op = "<" if q < 0.5 else ">"
            for d in (+1, -1):
                rule = (name, op, float(thr), d)
                cond = (x < thr) if op == "<" else (x > thr)
                _, p, z = hit(d, cond, ref["out"], ref["tr"])
                if p == p:
                    singles.append((z, rule))
    singles.sort(reverse=True, key=lambda r: r[0])
    n_rules = len(singles)
    print(f"single rules scored on train: {n_rules} "
          f"(chance max z ~= {np.sqrt(2*np.log(n_rules)):.1f})")

    # ---- stage 2: validation on both symbols ----------------------------
    def apply(sym, rule, mask_key):
        name, op, thr, d = rule
        x = data[sym]["f"][name]
        cond = (x < thr) if op == "<" else (x > thr)
        return hit(d, cond, data[sym]["out"], data[sym][mask_key])

    survivors = []
    for z_tr, rule in singles[:60]:
        vals = [apply(s, rule, "va") for s in SYMS]
        if all(v[1] == v[1] and v[2] > 1.5 for v in vals):
            survivors.append((min(v[2] for v in vals), rule, vals))
    survivors.sort(reverse=True, key=lambda r: r[0])
    survivors = survivors[:TOP_SINGLE]
    print(f"\nvalidation survivors (z>1.5 on BOTH symbols): {len(survivors)}")
    for _, rule, vals in survivors[:12]:
        name, op, thr, d = rule
        vd = "  ".join(f"{s[:3]} n={v[0]} {v[1]:.1%}" for s, v in zip(SYMS, vals))
        print(f"  {name}{op}{thr:.2f} -> {'UP' if d>0 else 'DOWN':<4} | {vd}")

    # ---- stage 3: pairwise combos of survivors, scored on validation ----
    combos = []
    rl = [r for _, r, _ in survivors]
    for i in range(len(rl)):
        for j in range(i + 1, len(rl)):
            a, b = rl[i], rl[j]
            if a[3] != b[3] or a[0] == b[0]:
                continue
            vals = []
            ok = True
            for s in SYMS:
                xa, xb = data[s]["f"][a[0]], data[s]["f"][b[0]]
                ca = (xa < a[2]) if a[1] == "<" else (xa > a[2])
                cb = (xb < b[2]) if b[1] == "<" else (xb > b[2])
                v = hit(a[3], ca & cb, data[s]["out"], data[s]["va"])
                if v[1] != v[1]:
                    ok = False; break
                vals.append(v)
            if ok:
                combos.append((min(v[2] for v in vals), (a, b), vals))
    combos.sort(reverse=True, key=lambda r: r[0])
    combos = combos[:TOP_COMBO]

    # ---- stage 4: FINAL TEST (read once) ---------------------------------
    print("\n================ FINAL TEST (held-out last 25%) ================")
    print("-- top singles --")
    for _, rule, _ in survivors[:8]:
        name, op, thr, d = rule
        row = []
        for s in SYMS:
            n_, p_, z_ = apply(s, rule, "te")
            row.append(f"{s[:3]}: n={n_} {p_ if p_==p_ else 0:.1%} z={z_ if z_==z_ else 0:+.1f}")
        print(f"  {name}{op}{thr:.2f}->{'UP' if d>0 else 'DOWN':<4} | " + " | ".join(row))
    print("-- top combos --")
    for _, (a, b), _ in combos[:6]:
        row = []
        for s in SYMS:
            xa, xb = data[s]["f"][a[0]], data[s]["f"][b[0]]
            ca = (xa < a[2]) if a[1] == "<" else (xa > a[2])
            cb = (xb < b[2]) if b[1] == "<" else (xb > b[2])
            n_, p_, z_ = hit(a[3], ca & cb, data[s]["out"], data[s]["te"])
            row.append(f"{s[:3]}: n={n_} {p_ if p_==p_ else 0:.1%} z={z_ if z_==z_ else 0:+.1f}")
        print(f"  {a[0]}{a[1]}{a[2]:.2f} & {b[0]}{b[1]}{b[2]:.2f} "
              f"->{'UP' if a[3]>0 else 'DOWN'} | " + " | ".join(row))

    # ---- ML --------------------------------------------------------------
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    print("\n-- ML (all features pooled) --")
    for s in SYMS:
        F = data[s]["f"]; out = data[s]["out"]
        X = np.column_stack([F[k] for k in feat_names])
        ok = ~np.isnan(X).any(1) & (out != 0)
        y = (out > 0).astype(int)
        tr, va, te = data[s]["tr"], data[s]["va"], data[s]["te"]
        clf = HistGradientBoostingClassifier(max_iter=400, max_depth=4,
                                             learning_rate=0.05,
                                             early_stopping=False,
                                             random_state=0)
        clf.fit(X[ok & tr], y[ok & tr])
        def auc(m): return roc_auc_score(y[ok & m], clf.predict_proba(X[ok & m])[:, 1])
        pr = clf.predict_proba(X[ok & te])[:, 1]
        yt = y[ok & te]
        conf = np.abs(pr - 0.5)
        m20 = conf >= np.quantile(conf, 0.8)
        acc20 = ((pr[m20] > 0.5).astype(int) == yt[m20]).mean()
        print(f"  {s}: val AUC={auc(va):.3f}  TEST AUC={auc(te):.3f}  "
              f"test top-20%-confidence acc={acc20:.1%} (n={int(m20.sum())})")
