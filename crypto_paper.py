"""crypto_paper.py — REAL Bybit demo bracket battery across many USDT perps.

The crypto twin of equity_paper.place_live_orders(): the symbol-agnostic zone
bracket strategies (gaptrav / gaptrav_tight / far_targets / meanrev_confluence) run
across a wide Bybit USDT-perp universe on LOW timeframes, and each fresh signal
places a REAL Bybit demo limit entry with broker-held TP/SL
(bybit_orders.place_entry_bracket, hedge mode). Crypto is 24/7, so unlike equities
there is no market-hours gate — resting brackets go out round the clock. The broker
is the source of truth (fills + realized P&L from Bybit); NO binance_sim, NO
self-resolved replay. See the broker-API-fills-only rule.

Bybit allows at most ONE long + ONE short position per symbol (hedge mode), shared
with the TradingView ideas book, so the battery takes the FIRST fresh signal per
(symbol, side) each run and skips the rest — the live exchange position
(position_by_idx) is the dedup source of truth, which also stops it colliding with
an ideas-book position on the same symbol/side.

Binary next-close algos (meanrev / wick_fade / zone_break_bias) have no broker-held
bracket form, so they are intentionally NOT on Bybit — only the bracket family is.
"""
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

import db
import strategies as S
import bybit_orders
from equity_paper import equity_zones
from rlab import registry

SYMBOLS = os.environ.get("CRYPTO_SYMBOLS",
    "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,"
    "DOTUSDT,POLUSDT,LTCUSDT,BCHUSDT,ATOMUSDT,NEARUSDT,APTUSDT,ARBUSDT,OPUSDT,"
    "INJUSDT,SUIUSDT,SEIUSDT,TIAUSDT,RUNEUSDT,FILUSDT,AAVEUSDT,UNIUSDT,ETCUSDT,"
    "XLMUSDT,ICPUSDT,HBARUSDT,VETUSDT,ALGOUSDT,GRTUSDT,SANDUSDT,MANAUSDT,AXSUSDT,"
    "THETAUSDT,GALAUSDT,CHZUSDT,CRVUSDT,LDOUSDT,DYDXUSDT,PENDLEUSDT,ENAUSDT,"
    "WLDUSDT,JUPUSDT,PYTHUSDT,WIFUSDT,SUSDT,STXUSDT,"
    # extended universe (more 24/7 throughput for data)
    "1000PEPEUSDT,1000BONKUSDT,1000FLOKIUSDT,ORDIUSDT,JTOUSDT,ALTUSDT,STRKUSDT,"
    "ETHFIUSDT,ZROUSDT,ZKUSDT,WUSDT,BOMEUSDT,NOTUSDT,IOUSDT,SAGAUSDT,TAOUSDT,"
    "AEVOUSDT,DYMUSDT,MANTAUSDT,JASMYUSDT,GMTUSDT,APEUSDT,FLOWUSDT,EGLDUSDT,"
    "KAVAUSDT,ROSEUSDT,1INCHUSDT,COMPUSDT,SNXUSDT,ENJUSDT,IOTAUSDT,QNTUSDT,"
    "KSMUSDT,WOOUSDT,GMXUSDT,ARKMUSDT,BLURUSDT,SUSHIUSDT,YGGUSDT,MASKUSDT,"
    "CFXUSDT,IMXUSDT,MINAUSDT,ARUSDT").split(",")

ZONE_TFS = ["5m"]                                   # ≤5min only (user mandate 2026-06-21)
TF_CODE = {"5m": "5", "15m": "15", "1h": "60"}      # Bybit kline interval codes
SCAN_BARS = int(os.environ.get("CRYPTO_SCAN_BARS", "260"))

# display base -> signal base. The bracket family that maps to a broker-held TP/SL.
# Deduped 2026-06-21: gaptrav_tight / far_targets / meanrev_confluence were
# near-identical zone/gap variants of gaptrav (stop/target tweaks) — collapsed to
# the one representative. New, genuinely-different families get added via the lab.
BRACKET = {"gaptrav_cx": "gaptrav"}

_KLINE = bybit_orders.BASE + "/v5/market/kline"


def _specs():
    """Yield (strategy_name, signal_base, tf) for every bracket strat × low tf."""
    for disp, sig in BRACKET.items():
        for tf in ZONE_TFS:
            yield f"{disp}_{tf}", sig, tf


def _klines(symbol, tf, limit=SCAN_BARS):
    """Recent Bybit linear klines, oldest->newest. Bybit returns newest-first."""
    p = urllib.parse.urlencode({"category": "linear", "symbol": symbol,
                                "interval": TF_CODE[tf], "limit": min(limit, 1000)})
    raw = json.loads(urllib.request.urlopen(_KLINE + "?" + p, timeout=20).read())
    lst = (raw.get("result") or {}).get("list") or []
    if not lst:
        return None
    rows = [{"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in lst]
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


def _bracket(base, df, bands):
    try:
        if base == "gaptrav":
            return S.gaptrav_open(df, bands)
        if base == "gaptrav_tight":
            return S.gaptrav_tight_open(df, bands)
        if base == "far_targets":
            return S.far_targets_open(df, bands)
        if base == "meanrev_confluence":
            return S.meanrev_confluence_open(df, bands)
    except Exception:
        return None
    return None


def ensure_manifests():
    made = 0
    for name, base, tf in _specs():
        if os.path.exists(registry.manifest_path(name)):
            continue
        registry.save_manifest({
            "name": name, "order": 20, "label": f"{base} · {tf} · crypto (Bybit)",
            "domain": "crypto", "kind": "bracket", "venue": "bybit_demo",
            "status": "research · data-collection", "lifecycle": "research",
            "role": "data-collection", "symbols": ",".join(SYMBOLS),
            "data": {"adapter": "bybit", "symbols": SYMBOLS, "timeframe": tf},
            "signal": {"module": "crypto_paper", "fn": "place_live_orders",
                       "live_collected": True, "params": {}, "param_grid": {}},
            "exec_model": "bracket_sltp",
            "gate": dict(registry.DEFAULT_GATE),
            "method": f"The {base} zone signal run on Bybit demo USDT perps at {tf} "
                      f"across {len(SYMBOLS)} symbols, each firing a REAL broker limit "
                      f"entry with broker-held TP/SL (24/7, hedge mode).",
            "risk": "Paper (Bybit demo). Broker-confirmed fills + realized P&L; no "
                    "self-resolve, no binance_sim.",
            "provenance": {"created_by": "agent", "date": "2026-06-19",
                           "hypothesis": f"{base} has a tradable edge on crypto perps at {tf}.",
                           "research_refs": ["crypto_paper.py", "bybit_orders.py"]}})
        made += 1
    return made


def place_live_orders():
    """Run the bracket battery across SYMBOLS × low TFs; place a REAL Bybit demo
    bracket for the first fresh signal per (symbol, side). Returns # placed."""
    if not bybit_orders.armed():
        return 0
    bybit_orders.ensure_hedge_mode()             # idempotent; battery uses hedge brackets
    placed = 0
    taken = set()                                # (symbol, dir) claimed this run
    bar_cache, band_cache, pos_cache = {}, {}, {}
    fmt = bybit_orders._fmt

    def _bars(sym, tf):
        k = (sym, tf)
        if k not in bar_cache:
            try:
                bar_cache[k] = _klines(sym, tf)
            except Exception:
                bar_cache[k] = None
        return bar_cache[k]

    def _bands(sym, tf, df):
        k = (sym, tf)
        if k not in band_cache:
            band_cache[k] = equity_zones(df)
        return band_cache[k]

    def _open_side(sym, d):
        """(symbol, side) already live? resting/unresolved execution OR a live
        exchange position on that hedge side (guards vs the ideas book too)."""
        side = "long" if d > 0 else "short"
        if db._rows("SELECT id FROM executions WHERE venue='bybit_demo' AND "
                    f"outcome='' AND symbol={db.PH} AND side={db.PH}", (sym, side)):
            return True
        k = (sym, d)
        if k not in pos_cache:
            pos = bybit_orders.position_by_idx(sym, d)
            try:
                pos_cache[k] = bool(pos and float(pos.get("size") or 0) > 0)
            except Exception:
                pos_cache[k] = False
        return pos_cache[k]

    # Symbol-major, with the first-pick algo ROTATED per symbol: the exchange caps a
    # symbol at 1 long + 1 short, so whichever algo fires first claims the slot. If we
    # always tried gaptrav first it would win every slot and the others would starve;
    # rotating makes each bracket algo lead on ~1/N of symbols so all of them trade.
    bases = list(BRACKET.items())                 # [(disp, sig_base), ...]
    nb = len(bases)
    for si, sym in enumerate(SYMBOLS):
        order = bases[si % nb:] + bases[:si % nb]
        for disp, base in order:
            for tf in ZONE_TFS:
                name = f"{disp}_{tf}"
                df = _bars(sym, tf)
                if df is None or len(df) < 60:
                    continue
                bands = _bands(sym, tf, df)
                if not bands:
                    continue
                entry = float(df["close"].iloc[-1])
                tr = _bracket(base, df, bands)
                if not tr:
                    continue
                d = tr["direction"]
                if (tr["target"] - entry) * d <= 0 or (entry - tr["stop"]) * d <= 0:
                    continue
                if (sym, d) in taken or _open_side(sym, d):
                    continue                      # 1 long + 1 short per symbol (hedge)
                it = bybit_orders._instr(sym)
                if not it:
                    continue
                qty, _risk = bybit_orders.position_qty(entry, tr["stop"], it)
                if qty <= 0:
                    continue
                oid, msg = bybit_orders.place_entry_bracket(
                    sym, d, fmt(entry, it["tick"]), fmt(qty, it["qty_step"]),
                    fmt(tr["target"], it["tick"]), fmt(tr["stop"], it["tick"]))
                if not oid:
                    print(f"  [crypto] {name} {sym} reject: {msg}")
                    continue
                sid = db.record_signal(name, sym, tf, d, tr.get("rule", base),
                                       detail={"tf": tf, "live_order": True})
                db.record_execution(sid, "bybit_demo", sym, "long" if d > 0 else "short",
                                    round(entry, 6), round(float(tr["stop"]), 6),
                                    round(float(tr["target"]), 6), ref=f"byb:{oid}")
                taken.add((sym, d))
                placed += 1
                print(f"  [crypto] {name} {'LONG' if d>0 else 'SHORT'} {sym} "
                      f"@ {fmt(entry, it['tick'])} tp={fmt(tr['target'], it['tick'])} "
                      f"sl={fmt(tr['stop'], it['tick'])} (order {oid})")
    return placed


def resolve_open():
    """Resolve battery executions from Bybit ground truth (hedge-aware): entry order
    cancelled/rejected → void; filled + position still open → wait; position closed →
    realized exit + P&L from closed-pnl. Returns # resolved."""
    if not bybit_orders.armed():
        return 0
    rows = db._rows("SELECT * FROM executions WHERE venue='bybit_demo' AND outcome='' "
                    f"AND ref LIKE {db.PH}", ("byb:%",))
    n = 0
    for e in rows:
        oid = (e.get("ref") or "")[len("byb:"):]
        if not oid:
            continue
        sym = e["symbol"]; d = 1 if e["side"] == "long" else -1
        o = bybit_orders.order_obj(sym, oid)
        status = o.get("orderStatus") if o else None
        if status in ("New", "PartiallyFilled", "Untriggered"):
            continue                              # entry still resting
        if status in ("Cancelled", "Rejected", "Deactivated"):
            db.resolve_execution(e["id"], float(e["entry"] or 0), "void", 0, 0.0, 0)
            n += 1
            continue
        pos = bybit_orders.position_by_idx(sym, d)
        if pos and float(pos.get("size") or 0) > 0:
            continue                              # filled, TP/SL still riding
        cp = bybit_orders.closed_pnl_recent(sym)
        if not cp:
            continue
        entry = float(cp.get("avgEntryPrice") or e["entry"])
        exitp = float(cp.get("avgExitPrice") or entry)
        pnl = float(cp.get("closedPnl") or 0)
        ret = d * (exitp - entry) / entry * 1e4 if entry else 0.0
        outcome = ("target" if abs(exitp - float(e["target"]))
                   <= abs(exitp - float(e["stop"])) else "stop")
        db.resolve_execution(e["id"], round(exitp, 6), outcome,
                             int(pnl > 0), round(ret, 1), 0)
        n += 1
    return n


if __name__ == "__main__":
    import sys
    db.init()
    m = ensure_manifests()
    r = resolve_open()
    p = 0 if "--resolve" in sys.argv else place_live_orders()
    print(f"crypto_paper: manifests+{m}, resolved {r}, placed {p}")
