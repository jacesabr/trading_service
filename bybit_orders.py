"""bybit_orders.py — REAL Bybit DEMO trading orders (server-side paper, v5 API).

The crypto analog of equity_orders.py: instead of resolving crypto ideas from 1m
klines locally (binance_sim — bug-prone, no external source of truth), we place a
LIMIT entry at the author's price on Bybit's **demo** venue (api-demo.bybit.com,
USDT perps / category=linear) with the **take-profit and stop-loss attached to the
order** — the BROKER holds and fills both exits. Long AND short. The exchange is the
source of truth: fills, TP/SL and realized P&L all come from Bybit.

Lifecycle is recorded in executions.ref (no new columns):
  byb:<orderId>     limit entry resting / filled (TP+SL ride on the position)
  -> resolve_execution() once the position closes (TP or SL fills) or it voids.

Bybit demo validates from any region (unlike Binance futures testnet), so this runs
locally and on the Render cron alike. Gated: BYBIT_DEMO_ORDERS=1 (+ keys). Demo USDT
only — never real funds. Keys: BYBIT_DEMO_KEY / BYBIT_DEMO_SECRET.
"""
import hashlib
import hmac
import json
import math
import os
import time
import urllib.error
import urllib.request

import db

BASE = os.environ.get("BYBIT_DEMO_BASE", "https://api-demo.bybit.com")
NOTIONAL_USDT = float(os.environ.get("BYBIT_NOTIONAL", "200"))   # per trade
RECV = "5000"
CAT = "linear"                                                   # USDT perpetuals


def _keys():
    return os.environ.get("BYBIT_DEMO_KEY"), os.environ.get("BYBIT_DEMO_SECRET")


def armed():
    k, s = _keys()
    return os.environ.get("BYBIT_DEMO_ORDERS") == "1" and bool(k and s)


# ─── signed v5 transport ───────────────────────────────────────────────────────
def _req(method, path, params=None):
    key, sec = _keys()
    if not (key and sec):
        return {"retCode": -1, "retMsg": "no BYBIT_DEMO_KEY/SECRET"}
    ts = str(int(time.time() * 1000))
    if method == "GET":
        qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        payload = qs
        url = f"{BASE}{path}" + (f"?{qs}" if qs else "")
        data = None
    else:
        payload = json.dumps(params or {}, separators=(",", ":"))
        url = f"{BASE}{path}"
        data = payload.encode()
    sign = hmac.new(sec.encode(), (ts + key + RECV + payload).encode(),
                    hashlib.sha256).hexdigest()
    headers = {"X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": ts,
               "X-BAPI-RECV-WINDOW": RECV, "X-BAPI-SIGN": sign,
               "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except urllib.error.HTTPError as e:
        return {"retCode": e.code, "retMsg": e.read().decode()[:200]}
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e)[:200]}


# ─── instrument filters → correct rounding (silent-reject guard) ───────────────
_INSTR = {}


def _instr(symbol):
    if symbol not in _INSTR:
        r = _req("GET", "/v5/market/instruments-info",
                 {"category": CAT, "symbol": symbol})
        lst = (r.get("result") or {}).get("list") or []
        if not lst:
            _INSTR[symbol] = None
        else:
            it = lst[0]
            pf = it.get("priceFilter", {}); lf = it.get("lotSizeFilter", {})
            _INSTR[symbol] = dict(
                tick=float(pf.get("tickSize", "0.1")),
                qty_step=float(lf.get("qtyStep", "0.001")),
                min_qty=float(lf.get("minOrderQty", "0")),
                min_notional=float(lf.get("minNotionalValue", "5") or 5))
    return _INSTR[symbol]


def _floor_step(v, step):
    return math.floor(v / step) * step if step > 0 else v


def _dec(step):
    s = repr(step)
    return len(s.split(".")[1]) if "." in s else 0


def _fmt(v, step):
    return f"{v:.{_dec(step)}f}"


# ─── place a bracket entry (TP/SL ride on the order, broker-held) ──────────────
def has_open(strat, symbol):
    rows = db._rows(
        "SELECT e.id FROM executions e JOIN signals s ON e.signal_id=s.id "
        f"WHERE e.venue='bybit_demo' AND e.outcome='' AND e.symbol={db.PH} "
        f"AND s.strategy={db.PH}", (symbol, strat))
    return bool(rows)


def place_bracket(strat, signal_id, symbol, direction, entry, stop, target,
                  notional=None):
    """Submit a REAL Bybit demo LIMIT entry with attached TP/SL. Returns exec_id or
    None. No-op unless armed + valid geometry + not already open (strategy,symbol)."""
    if not armed():
        return None
    if (target - entry) * direction <= 0 or (entry - stop) * direction <= 0:
        return None
    if has_open(strat, symbol):
        return None
    it = _instr(symbol)
    if not it:
        print(f"  [bybit] no instrument {symbol}")
        return None
    qty = _floor_step((notional or NOTIONAL_USDT) / entry, it["qty_step"])
    if qty < it["min_qty"] or qty * entry < it["min_notional"]:
        print(f"  [bybit] {symbol} qty {qty} below min "
              f"(minQty {it['min_qty']}, minNotional {it['min_notional']})")
        return None
    body = {"category": CAT, "symbol": symbol,
            "side": "Buy" if direction > 0 else "Sell", "orderType": "Limit",
            "qty": _fmt(qty, it["qty_step"]), "price": _fmt(entry, it["tick"]),
            "timeInForce": "GTC", "tpslMode": "Full",
            "takeProfit": _fmt(target, it["tick"]), "stopLoss": _fmt(stop, it["tick"]),
            "tpTriggerBy": "LastPrice", "slTriggerBy": "LastPrice"}
    r = _req("POST", "/v5/order/create", body)
    oid = (r.get("result") or {}).get("orderId")
    if r.get("retCode") != 0 or not oid:
        print(f"  [bybit] entry reject {symbol}: {r.get('retMsg')}")
        return None
    eid = db.record_execution(signal_id, "bybit_demo", symbol,
                              "long" if direction > 0 else "short",
                              round(float(entry), 6), round(float(stop), 6),
                              round(float(target), 6), ref=f"byb:{oid}")
    print(f"  [bybit] ENTRY {'Buy' if direction>0 else 'Sell'} {symbol} "
          f"qty={_fmt(qty, it['qty_step'])} @ {_fmt(entry, it['tick'])} "
          f"tp={_fmt(target, it['tick'])} sl={_fmt(stop, it['tick'])} "
          f"(order {oid}, exec {eid})")
    return eid


# ─── resolve: poll order + position; close out on TP/SL fill ───────────────────
def _order(symbol, oid):
    r = _req("GET", "/v5/order/realtime",
             {"category": CAT, "symbol": symbol, "orderId": oid})
    lst = (r.get("result") or {}).get("list") or []
    return lst[0] if lst else None


def _position_size(symbol):
    r = _req("GET", "/v5/position/list", {"category": CAT, "symbol": symbol})
    lst = (r.get("result") or {}).get("list") or []
    try:
        return float(lst[0]["size"]) if lst else 0.0
    except Exception:
        return None


def _last_closed(symbol):
    r = _req("GET", "/v5/position/closed-pnl",
             {"category": CAT, "symbol": symbol, "limit": "1"})
    lst = (r.get("result") or {}).get("list") or []
    return lst[0] if lst else None


def resolve_open():
    if not all(_keys()):
        return 0
    rows = db._rows("SELECT * FROM executions WHERE venue='bybit_demo' AND outcome=''")
    n = 0
    for e in rows:
        oid = (e.get("ref") or "")[len("byb:"):]
        if not oid:
            continue
        sym = e["symbol"]; d = 1 if e["side"] == "long" else -1
        o = _order(sym, oid)
        status = o.get("orderStatus") if o else None
        # entry still working & not yet a position → wait (or void if dead)
        if status in ("New", "PartiallyFilled", "Untriggered"):
            continue
        if status in ("Cancelled", "Rejected", "Deactivated"):
            db.resolve_execution(e["id"], float(e["entry"] or 0), "void", 0, 0.0, 0)
            print(f"  [bybit] VOID {sym} entry {status} (exec {e['id']})")
            n += 1
            continue
        # entry Filled (or already gone) → is the position still open?
        size = _position_size(sym)
        if size is None:
            continue
        if size > 0:
            continue                                   # TP/SL still riding
        # position closed → realized result from closed-pnl
        cp = _last_closed(sym)
        if not cp:
            continue
        entry = float(cp.get("avgEntryPrice") or e["entry"])
        exitp = float(cp.get("avgExitPrice") or entry)
        pnl = float(cp.get("closedPnl") or 0)
        ret = d * (exitp - entry) / entry * 1e4 if entry else 0.0
        # label by which level the exit sits closer to
        outcome = ("target" if abs(exitp - float(e["target"]))
                   <= abs(exitp - float(e["stop"])) else "stop")
        db.resolve_execution(e["id"], round(exitp, 6), outcome,
                             int(pnl > 0), round(ret, 1), 0)
        print(f"  [bybit] CLOSED {sym} {outcome} pnl={pnl:+.2f} {ret:+.1f}bps "
              f"(exec {e['id']})")
        n += 1
    return n


# ─── hedge-mode per-idea primitives (used by the TradingView ideas path) ───────
# Hedge mode (positionIdx 1=long, 2=short) + reduce-only conditional exits let
# MANY ideas share a symbol, each with its own broker-held TP/SL — so every crypto
# idea is broker-confirmed (no binance_sim, no netting collisions).
def ensure_hedge_mode():
    """One-time: switch USDT perps to both-side (hedge) mode. Safe to re-call."""
    return _req("POST", "/v5/position/switch-mode",
                {"category": CAT, "coin": "USDT", "mode": 3})


def order_obj(symbol, oid):
    """The order by id from realtime (open/untriggered) or history (done)."""
    for path in ("/v5/order/realtime", "/v5/order/history"):
        r = _req("GET", path, {"category": CAT, "symbol": symbol, "orderId": oid})
        lst = (r.get("result") or {}).get("list") or []
        if lst:
            return lst[0]
    return None


def cancel(symbol, oid):
    return _req("POST", "/v5/order/cancel",
                {"category": CAT, "symbol": symbol, "orderId": oid})


def place_entry(symbol, direction, entry_str, qty_str):
    """LIMIT entry in hedge mode (no TP/SL — exits attach on fill). Returns id/None."""
    body = {"category": CAT, "symbol": symbol, "side": "Buy" if direction > 0 else "Sell",
            "orderType": "Limit", "positionIdx": 1 if direction > 0 else 2,
            "qty": qty_str, "price": entry_str, "timeInForce": "GTC"}
    r = _req("POST", "/v5/order/create", body)
    return (r.get("result") or {}).get("orderId") if r.get("retCode") == 0 else None


def place_reduce_conditional(symbol, direction, qty_str, trigger_str, kind):
    """A reduce-only conditional market that closes this idea's qty when the
    TP/SL trigger prints. kind='tp'|'sl'. Returns (orderId, retMsg)."""
    exit_side = "Sell" if direction > 0 else "Buy"
    pos_idx = 1 if direction > 0 else 2
    # long: TP above (price rises → dir 1), SL below (falls → 2); short: mirrored
    if direction > 0:
        td = 1 if kind == "tp" else 2
    else:
        td = 2 if kind == "tp" else 1
    r = _req("POST", "/v5/order/create",
             {"category": CAT, "symbol": symbol, "side": exit_side,
              "orderType": "Market", "qty": qty_str, "positionIdx": pos_idx,
              "reduceOnly": True, "triggerPrice": trigger_str,
              "triggerDirection": td, "triggerBy": "LastPrice"})
    return (r.get("result") or {}).get("orderId"), r.get("retMsg")


def probe():
    if not all(_keys()):
        return {"ok": False, "why": "no BYBIT_DEMO_KEY/SECRET"}
    r = _req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    if r.get("retCode") == 0:
        eq = [(a.get("totalEquity"), a.get("totalWalletBalance"))
              for a in (r["result"]["list"])]
        return {"ok": True, "armed": armed(), "equity": eq,
                "btc": _instr("BTCUSDT")}
    return {"ok": False, "error": r}


if __name__ == "__main__":
    print("base:", BASE, "| armed:", armed(), "| notional:", NOTIONAL_USDT)
    print("probe:", json.dumps(probe(), indent=1))
