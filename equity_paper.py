"""equity_paper.py — run the strategy battery on REAL Alpaca equity bars.

Symbol- and timeframe-agnostic: the same signal functions in strategies.py run on
equities. Zone strategies (gaptrav / gaptrav_tight / far_targets / zone_break) run
across MULTIPLE timeframes; binary probes (meanrev / wick_fade) on 5m. Each
(strategy, timeframe) is a distinct DB strategy + manifest so the numbers stay
clean per timeframe; symbols are aggregated within each.

Paper, resolved from real Alpaca bars — same fidelity as the crypto paper path.
(True Alpaca paper-order fills via the executions table are the next upgrade.)

  ensure_manifests()  create any missing equity manifests (research lifecycle)
  collect()           record one signal per (strat,tf,symbol) at the last bar
  resolve_open()      settle open equity trades from subsequent Alpaca bars
"""
import os

import db
import strategies as S
from adapters.data import alpaca
from rlab import registry
from zone_breaks import compute_profile
from gap_traversal import ZONES

SYMBOLS = os.environ.get("EQUITY_SYMBOLS",
    # Tech / growth
    "SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL,AMD,NFLX,AVGO,PLTR,COIN,"
    "UBER,SHOP,MU,SOFI,RIVN,INTC,ARM,SMCI,DELL,CRM,NOW,"
    # Semis / hardware
    "TSM,QCOM,TXN,AMAT,LRCX,MCHP,ON,ANET,MRVL,"
    # Software / cloud
    "ORCL,ADBE,CSCO,PANW,SNOW,NET,DDOG,CRWD,"
    # Internet / consumer-discretionary
    "DIS,ABNB,BKNG,CMG,LULU,MELI,PYPL,EBAY,"
    # Comms / telecom
    "CMCSA,TMUS,T,VZ,"
    # Finance
    "JPM,BAC,GS,MS,V,MA,BRK.B,C,WFC,AXP,SCHW,BLK,COF,HOOD,"
    # Healthcare
    "JNJ,UNH,PFE,MRK,ABBV,LLY,TMO,ABT,DHR,AMGN,GILD,ISRG,"
    # Energy / materials
    "XOM,CVX,OXY,SLB,FCX,COP,"
    # Consumer / retail
    "WMT,COST,TGT,NKE,SBUX,MCD,HD,"
    # Industrials / defence
    "CAT,BA,LMT,RTX,GE,HON,UPS,DE,"
    # Crypto-adjacent equities
    "MARA,RIOT,CLSK,MSTR,"
    # ETFs (sector / index)
    "XLF,XLE,XLV,XLK,XLI,ARKK,IWM,DIA,SMH,XBI,GLD,XLU").split(",")
LTF = {"5m": "1m", "15m": "5m", "1h": "15m"}
ZONE_TFS = ["5m"]                                   # ≤5min only (user mandate 2026-06-21)
BRACKET_MAXBARS = 24

# display base -> signal base (the fn key used by _bracket/_binary).
# Zone/bracket run on ZONE_TFS; binary on 5m.
ZONE_BRACKET = {
    "gaptrav_eq": "gaptrav",          # deduped: tight/far_targets were gaptrav variants
}
BINARY = {"meanrev_eq": "meanrev", "wick_fade_eq": "wick_fade"}  # 5m only; 2 distinct families
# zone_break dropped on equities: its profile engine needs dense 24/7 ltf
# coverage that equity overnight gaps starve. gaptrav family uses equity_zones().


def _specs():
    """Yield (strategy_name, signal_base, tf, kind)."""
    for disp, sig in ZONE_BRACKET.items():
        for tf in ZONE_TFS:
            yield f"{disp}_{tf}", sig, tf, "bracket"
    for disp, sig in BINARY.items():
        yield f"{disp}_5m", sig, "5m", "binary"


def ensure_manifests():
    made = 0
    for name, base, tf, kind in _specs():
        if os.path.exists(registry.manifest_path(name)):
            continue
        registry.save_manifest({
            "name": name, "order": 20, "label": f"{base} · {tf} · equities",
            "domain": "equity", "kind": kind, "venue": "alpaca",
            "status": "research · data-collection", "lifecycle": "research",
            "role": "data-collection", "symbols": ",".join(SYMBOLS),
            "data": {"adapter": "alpaca", "symbols": SYMBOLS, "timeframe": tf,
                     "ltf": LTF.get(tf)},
            "signal": {"module": "equity_paper", "fn": "collect",
                       "live_collected": True, "params": {}, "param_grid": {}},
            "exec_model": "bracket_sltp" if kind == "bracket" else "spot_bps",
            "gate": dict(registry.DEFAULT_GATE),
            "method": f"The {base} signal run on liquid US equities at {tf} on real "
                      f"Alpaca bars, across {len(SYMBOLS)} symbols. Tests whether the "
                      f"zone/reversion logic transfers from crypto to equities and "
                      f"which timeframe carries it.",
            "risk": "Paper, resolved from real Alpaca bars. Research/data-collection "
                    "— no edge claimed; gathering multi-symbol/timeframe numbers.",
            "provenance": {"created_by": "agent",
                           "date": "2026-06-15",
                           "hypothesis": f"{base} has a tradable edge on equities at {tf}.",
                           "research_refs": ["equity_paper.py", "strategies.py"]}})
        made += 1
    return made


def equity_zones(df, lookback=100, va=0.70):
    """Volume-profile fib zone bands from the chart bars themselves over the last
    `lookback` bars. Self-contained (no dense lower-timeframe feed) so it works on
    equities with overnight gaps, where the crypto ltf-coverage path starves."""
    w = df.tail(lookback)
    if len(w) < 40:
        return None
    h = w["high"].to_numpy(); l = w["low"].to_numpy(); v = w["volume"].to_numpy()
    prof = compute_profile(h, l, v, h, l, v, va)   # chart bars act as their own ltf
    if not prof:
        return None
    val, vah = prof; rng = vah - val
    if rng <= 0:
        return None
    return [(val + rng * b / 100, val + rng * tp / 100) for b, tp in ZONES]


def _has_open(strategy, symbol):
    return bool(db._rows(
        "SELECT t.id FROM trades t JOIN signals s ON t.signal_id=s.id "
        f"WHERE s.strategy='{strategy}' AND t.symbol='{symbol}' AND t.outcome=''"))


def _binary(base, df):
    if base == "meanrev":
        side, rule = S.meanrev_signal(df)
        return (1 if side == "Up" else -1, rule) if side else (None, None)
    if base == "wick_fade":
        return S.wick_fade_signal(df)
    return None, None


def _bracket(base, df, bands):
    if base == "gaptrav":
        return S.gaptrav_open(df, bands)
    if base == "gaptrav_tight":
        return S.gaptrav_tight_open(df, bands)
    if base == "far_targets":
        return S.far_targets_open(df, bands)
    return None


SCAN = int(os.environ.get("EQUITY_SCAN_BARS", "120"))   # bars/run to backfill
MAX_PER_RUN = int(os.environ.get("EQUITY_MAX_PER_RUN", "4000"))


def _last_ts(strategy, symbol):
    r = db._rows("SELECT MAX(ts) m FROM signals "
                 f"WHERE strategy='{strategy}' AND symbol='{symbol}'")
    return int(r[0]["m"]) if r and r[0]["m"] else 0


def collect(probe=False):
    """Backfill: record every signal that fired on bars since the last recorded
    one for each (strategy, symbol). A periodic job (every 8h) thus captures all
    the bar-close signals in the interval, not just the instant it happens to run.
    Resolution settles them from subsequent real Alpaca bars."""
    ensure_manifests()
    placed = 0
    batch = []                                   # accumulate, flush in one write
    cache = {}

    def _bars(sym, tf):
        k = (sym, tf)
        if k not in cache:
            try:
                cache[k] = alpaca.bars(sym, tf, max(SCAN + 220, 260))
            except Exception:
                cache[k] = None
        return cache[k]

    for name, base, tf, kind in _specs():
        warm = 110 if kind == "bracket" else 210
        for sym in SYMBOLS:
            df = _bars(sym, tf)
            if df is None or len(df) < warm + 2:
                continue
            last = 0 if probe else _last_ts(name, sym)
            start = max(warm, len(df) - SCAN)
            prev_fired = False                   # edge-trigger: record on rising edge
            for i in range(start, len(df)):
                if len(batch) >= MAX_PER_RUN:
                    break
                w = df.iloc[:i + 1]
                entry = float(df["close"].iloc[i])
                # evaluate the signal at bar i
                d = rule = stop = tgt = None
                if kind == "bracket":
                    bands = equity_zones(w)
                    tr = _bracket(base, w, bands) if bands else None
                    if tr and (tr["target"] - entry) * tr["direction"] > 0 and \
                       (entry - tr["stop"]) * tr["direction"] > 0:
                        d = tr["direction"]; rule = tr.get("rule", base)
                        stop = round(tr["stop"], 4); tgt = round(tr["target"], 4)
                else:
                    dd, rr = _binary(base, w)
                    if dd:
                        d, rule = dd, rr
                fired = d is not None
                edge = fired and not prev_fired   # only a NEW signal counts
                prev_fired = fired
                bar_ts = int(df["ts"].iloc[i] // 1000)
                if not edge or bar_ts <= last:
                    continue
                if probe:
                    print(f"  {name} {sym} @bar{i}: "
                          f"{'long/up' if d>0 else 'short/down'} ({rule})")
                    continue
                batch.append(dict(strategy=name, symbol=sym, timeframe=tf,
                                  direction=d, rule=rule or base,
                                  detail={"tf": tf}, ts=bar_ts,
                                  side="long" if d > 0 else "short",
                                  entry=round(entry, 4), stop=stop, target=tgt))
    if probe:
        return placed
    return db.record_signals_trades(batch)       # one connection, one commit


def place_live_orders():
    """For allow-listed equity BRACKET strategies, place REAL Alpaca bracket
    orders on the LATEST (live) bar's signal only — separate from the backfill
    collect, which must never place an order per historical bar. The broker
    holds the OCO exit. No-op unless equity_orders.armed(). Returns # placed."""
    import equity_orders
    if not equity_orders.armed():
        return 0
    placed = 0
    # Cache bars + zones per (symbol, tf): the 3 bracket strategies all read the
    # SAME (sym, tf) data, so without this we'd hit Alpaca 3x per pair. At a large
    # symbol universe that re-fetch is what trips the data rate limit (and the
    # except-skip below would then silently drop later symbols). One fetch per pair.
    bar_cache, band_cache = {}, {}

    def _bars(sym, tf):
        k = (sym, tf)
        if k not in bar_cache:
            try:
                bar_cache[k] = alpaca.bars(sym, tf, max(SCAN + 220, 260))
            except Exception:
                bar_cache[k] = None
        return bar_cache[k]

    def _bands(sym, tf, df):
        k = (sym, tf)
        if k not in band_cache:
            band_cache[k] = equity_zones(df)
        return band_cache[k]

    for name, base, tf, kind in _specs():
        if kind != "bracket" or not equity_orders.allowed(name):
            continue
        warm = 110
        for sym in SYMBOLS:
            if equity_orders.has_open(name, sym):
                continue                              # one open bracket per sym
            df = _bars(sym, tf)
            if df is None or len(df) < warm + 2:
                continue
            bands = _bands(sym, tf, df)
            if not bands:
                continue
            entry = float(df["close"].iloc[-1])       # latest closed bar
            tr = _bracket(base, df, bands)
            if not tr:
                continue
            d = tr["direction"]
            if (tr["target"] - entry) * d <= 0 or (entry - tr["stop"]) * d <= 0:
                continue
            sid = db.record_signal(name, sym, tf, d, tr.get("rule", base),
                                   detail={"tf": tf, "live_order": True})
            if equity_orders.place_bracket(name, sid, sym, d, entry,
                                           tr["stop"], tr["target"]):
                placed += 1
    return placed


def resolve_open():
    rows = db._rows(
        "SELECT t.*, s.strategy, s.timeframe FROM trades t "
        "JOIN signals s ON t.signal_id=s.id "
        f"WHERE t.outcome='' AND s.strategy LIKE {db.PH}", ("%_eq_%",))
    ups = []
    cache = {}
    for t in rows:
        tf = t["timeframe"]; sym = t["symbol"]
        key = (sym, tf)
        if key not in cache:
            try:
                cache[key] = alpaca.bars(sym, tf, 400)
            except Exception:
                cache[key] = None
        df = cache[key]
        if df is None or df.empty:
            continue
        entry_ts_ms = int(t["ts"]) * 1000
        after = df[df["ts"] > entry_ts_ms].reset_index(drop=True)
        if after.empty:
            continue
        d = 1 if t["side"] == "long" else -1
        if t["target"] is None:                  # binary next-bar
            o = float(after["open"].iloc[0]); c = float(after["close"].iloc[0])
            up = c >= o
            won = int((up and d > 0) or (not up and d < 0))
            ret = d * (c - o) / o * 1e4
            ups.append((round(c, 4), "up" if up else "down", won,
                        round(ret, 1), 1, t["id"]))
        else:                                    # bracket
            stop, tgt = float(t["stop"]), float(t["target"])
            entry = float(t["entry"]); outcome = None
            for i, r in after.head(BRACKET_MAXBARS).iterrows():
                hi, lo, cl = float(r["high"]), float(r["low"]), float(r["close"])
                stop_hit = cl <= stop if d > 0 else cl >= stop
                tgt_hit = hi >= tgt if d > 0 else lo <= tgt
                if stop_hit:
                    outcome, exitp, held = "stop", stop, i + 1; break
                if tgt_hit:
                    outcome, exitp, held = "target", tgt, i + 1; break
            if outcome is None:
                if len(after) < BRACKET_MAXBARS:
                    continue                     # not enough bars yet; wait
                exitp = float(after["close"].iloc[BRACKET_MAXBARS - 1])
                outcome, held = "timeout", BRACKET_MAXBARS
            won = int(outcome == "target")
            ret = d * (exitp - entry) / entry * 1e4
            ups.append((round(exitp, 4), outcome, won, round(ret, 1), held,
                        t["id"]))
    return db.resolve_trades_batch(ups)          # one connection, executemany


if __name__ == "__main__":
    import sys
    db.init()
    if "--probe" in sys.argv:
        ensure_manifests()
        collect(probe=True)
    else:
        m = ensure_manifests()
        r = resolve_open()
        p = collect()
        print(f"equity_paper: manifests+{m}, resolved {r}, recorded {p}")
