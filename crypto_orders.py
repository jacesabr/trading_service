"""crypto_orders.py — REAL Binance USDⓈ-M FUTURES TESTNET orders (server-side paper).

The crypto analog of equity_orders.py: instead of resolving crypto ideas from 1m
klines locally (binance_sim — bug-prone, no external source of truth), we place a
LIMIT entry at the author's price on the Binance **futures testnet**
(testnet.binancefuture.com) and let the BROKER hold the STOP_MARKET / TAKE_PROFIT_
MARKET exits (closePosition). Long AND short (the spot testnet was long-only). The
exchange is the source of truth — fills, TP/SL and P&L all come from the venue.

Lifecycle, encoded in executions.ref so the schema needs no new columns:
  fent:<entryId>                  entry LIMIT resting / filled, exits not yet placed
  fbr:<entryId>:<tpId>:<slId>     position open, broker holds TP + SL
  -> resolve_execution() once the position closes (one exit fills) or it voids.

REGION: signed futures-testnet *trading* is geo-blocked from the US (public data is
not). Run the signed paths from the Frankfurt worker/cron — from a US box they 401/
-2015, which is expected, not a bad key. Gated: BINANCE_FUTURES_ORDERS=1 (+ keys).
Keys: BINANCE_FUTURES_TESTNET_KEY / BINANCE_FUTURES_TESTNET_SECRET. Money floor
untouched — this is fake testnet USDT, never real funds.
"""
import hashlib
import hmac
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import db

BASE = os.environ.get("BINANCE_FUTURES_BASE", "https://demo-fapi.binance.com")
NOTIONAL_USDT = float(os.environ.get("CRYPTO_FUTURES_NOTIONAL", "200"))  # per trade
LEVERAGE = int(os.environ.get("CRYPTO_FUTURES_LEVERAGE", "1"))
MAX_WAIT_S = int(os.environ.get("CRYPTO_FUTURES_MAX_WAIT_S", str(36 * 3600)))


def _keys():
    return (os.environ.get("BINANCE_FUTURES_KEY")
            or os.environ.get("BINANCE_FUTURES_TESTNET_KEY"),
            os.environ.get("BINANCE_FUTURES_SECRET")
            or os.environ.get("BINANCE_FUTURES_TESTNET_SECRET"))


def armed():
    k, s = _keys()
    return os.environ.get("BINANCE_FUTURES_ORDERS") == "1" and bool(k and s)


# ─── signed HTTP (fapi) ───────────────────────────────────────────────────────
def _http(url, headers=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        raw = urllib.request.urlopen(req, timeout=20).read().decode().strip()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_body": e.read().decode()[:300]}
    except Exception as e:
        return {"_err": -1, "_body": str(e)[:300]}


def server_time():
    r = _http(f"{BASE}/fapi/v1/time")
    return r.get("serverTime") if isinstance(r, dict) else None


def _signed(path, params=None, method="GET"):
    key, sec = _keys()
    if not (key and sec):
        return {"_err": -1, "_body": "no BINANCE_FUTURES_TESTNET_KEY/SECRET"}
    p = dict(params or {})
    p["timestamp"] = server_time() or int(time.time() * 1000)
    p["recvWindow"] = 5000
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(sec.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{qs}&signature={sig}"
    return _http(url, headers={"X-MBX-APIKEY": key}, method=method)


# ─── exchange filters → correct rounding (the #1 cause of order rejects) ───────
_FILTERS = {}


def _filters(symbol):
    if not _FILTERS:
        info = _http(f"{BASE}/fapi/v1/exchangeInfo")
        for s in (info.get("symbols") or []):
            f = {flt["filterType"]: flt for flt in s.get("filters", [])}
            _FILTERS[s["symbol"]] = dict(
                tick=float(f.get("PRICE_FILTER", {}).get("tickSize", "0.01")),
                step=float(f.get("LOT_SIZE", {}).get("stepSize", "0.001")),
                min_qty=float(f.get("LOT_SIZE", {}).get("minQty", "0")),
                min_notional=float(f.get("MIN_NOTIONAL", {}).get("notional", "5")),
                p_prec=s.get("pricePrecision", 2), q_prec=s.get("quantityPrecision", 3))
    return _FILTERS.get(symbol)


def _round_step(v, step):
    return math.floor(v / step) * step if step > 0 else v


def _fmt(v, prec):
    return f"{v:.{prec}f}"


# ─── leverage + orders ─────────────────────────────────────────────────────────
def _set_leverage(symbol):
    _signed("/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE}, "POST")


def _place_entry(symbol, side, qty, price, flt):
    return _signed("/fapi/v1/order", {
        "symbol": symbol, "side": side, "type": "LIMIT", "timeInForce": "GTC",
        "quantity": _fmt(qty, flt["q_prec"]), "price": _fmt(price, flt["p_prec"])},
        "POST")


def _place_exit(symbol, exit_side, otype, stop_price, flt):
    """STOP_MARKET / TAKE_PROFIT_MARKET that closes the whole position when hit."""
    return _signed("/fapi/v1/order", {
        "symbol": symbol, "side": exit_side, "type": otype,
        "stopPrice": _fmt(stop_price, flt["p_prec"]), "closePosition": "true",
        "workingType": "MARK_PRICE"}, "POST")


def _order(symbol, oid):
    return _signed("/fapi/v1/order", {"symbol": symbol, "orderId": oid})


def _cancel(symbol, oid):
    return _signed("/fapi/v1/order", {"symbol": symbol, "orderId": oid}, "DELETE")


def _position_amt(symbol):
    r = _signed("/fapi/v2/positionRisk", {"symbol": symbol})
    if isinstance(r, list) and r:
        try:
            return float(r[0].get("positionAmt", 0))
        except Exception:
            return None
    return None


# ─── place a bracket (entry now; exits attached once entry fills) ──────────────
def has_open(strat, symbol):
    rows = db._rows(
        "SELECT e.id FROM executions e JOIN signals s ON e.signal_id=s.id "
        f"WHERE e.venue='binance_futures' AND e.outcome='' AND e.symbol={db.PH} "
        f"AND s.strategy={db.PH}", (symbol, strat))
    return bool(rows)


def place_bracket(strat, signal_id, symbol, direction, entry, stop, target,
                  notional=None):
    """Submit a REAL futures-testnet LIMIT entry; exits are attached by
    resolve_open() once it fills. Returns exec_id or None. No-op unless armed +
    valid geometry + not already open for (strategy, symbol)."""
    if not armed():
        return None
    if (target - entry) * direction <= 0 or (entry - stop) * direction <= 0:
        return None
    if has_open(strat, symbol):
        return None
    flt = _filters(symbol)
    if not flt:
        print(f"  [crypto_orders] no exchange filters for {symbol}")
        return None
    qty = _round_step((notional or NOTIONAL_USDT) / entry, flt["step"])
    if qty < flt["min_qty"] or qty * entry < flt["min_notional"]:
        print(f"  [crypto_orders] {symbol} qty {qty} below min "
              f"(minQty {flt['min_qty']}, minNotional {flt['min_notional']})")
        return None
    _set_leverage(symbol)
    side = "BUY" if direction > 0 else "SELL"
    o = _place_entry(symbol, side, qty, entry, flt)
    if o.get("_err") or not o.get("orderId"):
        print(f"  [crypto_orders] entry reject {symbol} {side}: {o.get('_body', o)}")
        return None
    eid = db.record_execution(signal_id, "binance_futures", symbol,
                              "long" if direction > 0 else "short",
                              round(float(entry), 6), round(float(stop), 6),
                              round(float(target), 6), ref=f"fent:{o['orderId']}")
    print(f"  [crypto_orders] ENTRY {side} {symbol} qty={_fmt(qty, flt['q_prec'])} "
          f"@ {_fmt(entry, flt['p_prec'])} (order {o['orderId']}, exec {eid})")
    return eid


# ─── resolve: attach exits on fill; close out when an exit triggers ────────────
def resolve_open():
    if not (_keys()[0] and _keys()[1]):
        return 0
    rows = db._rows("SELECT * FROM executions WHERE venue='binance_futures' AND "
                    "outcome=''")
    n = 0
    now = int(time.time())
    for e in rows:
        ref = e.get("ref") or ""
        sym = e["symbol"]
        d = 1 if e["side"] == "long" else -1
        exit_side = "SELL" if d > 0 else "BUY"
        flt = _filters(sym) or {"p_prec": 2}

        if ref.startswith("fent:"):
            ent_id = ref.split(":", 1)[1]
            o = _order(sym, ent_id)
            if o.get("_err"):
                continue
            st = o.get("status")
            if st == "FILLED":
                # attach broker-held TP + SL, transition to fbr:
                real_entry = float(o.get("avgPrice") or e["entry"])
                tp = _place_exit(sym, exit_side, "TAKE_PROFIT_MARKET",
                                 float(e["target"]), flt)
                sl = _place_exit(sym, exit_side, "STOP_MARKET",
                                 float(e["stop"]), flt)
                if tp.get("orderId") and sl.get("orderId"):
                    db.update_execution_ref(
                        e["id"], f"fbr:{ent_id}:{tp['orderId']}:{sl['orderId']}",
                        entry=round(real_entry, 6))
                    print(f"  [crypto_orders] FILLED {sym} @ {real_entry} → exits "
                          f"TP {tp['orderId']} / SL {sl['orderId']} (exec {e['id']})")
                    n += 1
                else:
                    print(f"  [crypto_orders] exit place fail {sym}: "
                          f"tp={tp.get('_body', tp)} sl={sl.get('_body', sl)}")
            elif st in ("CANCELED", "EXPIRED", "REJECTED"):
                db.resolve_execution(e["id"], float(e["entry"] or 0), "void", 0, 0.0, 0)
                print(f"  [crypto_orders] VOID {sym} entry {st} (exec {e['id']})")
                n += 1
            elif (now - int(e.get("ts", now) or now)) > MAX_WAIT_S:
                _cancel(sym, ent_id)
                db.resolve_execution(e["id"], float(e["entry"] or 0), "void", 0, 0.0, 0)
                print(f"  [crypto_orders] EXPIRED unfilled {sym} (exec {e['id']})")
                n += 1
            continue

        if ref.startswith("fbr:"):
            _, ent_id, tp_id, sl_id = ref.split(":")
            amt = _position_amt(sym)
            if amt is None or abs(amt) > 0:
                continue                                # still in the trade
            # position is flat → an exit fired; find which
            tp = _order(sym, tp_id)
            sl = _order(sym, sl_id)
            entry = float(e["entry"])
            if tp.get("status") == "FILLED":
                exitp = float(tp.get("avgPrice") or e["target"]); outcome = "target"
                _cancel(sym, sl_id)
            elif sl.get("status") == "FILLED":
                exitp = float(sl.get("avgPrice") or e["stop"]); outcome = "stop"
                _cancel(sym, tp_id)
            else:
                # closed by neither recorded exit (manual/liquidation) — mark flat
                exitp = entry; outcome = "flat"
            ret = d * (exitp - entry) / entry * 1e4
            db.resolve_execution(e["id"], round(exitp, 6), outcome,
                                 int(outcome == "target"), round(ret, 1), 0)
            print(f"  [crypto_orders] CLOSED {sym} {outcome} {ret:+.1f}bps "
                  f"(exec {e['id']})")
            n += 1
    return n


def probe():
    """Connectivity + auth check (run from the Frankfurt worker). No order placed."""
    k, s = _keys()
    if not (k and s):
        return {"ok": False, "why": "no BINANCE_FUTURES_TESTNET_KEY/SECRET"}
    bal = _signed("/fapi/v2/balance")
    if isinstance(bal, list):
        usdt = next((b for b in bal if b.get("asset") == "USDT"), {})
        return {"ok": True, "armed": armed(), "usdt_balance": usdt.get("balance"),
                "btc_filters": _filters("BTCUSDT")}
    return {"ok": False, "error": bal}


if __name__ == "__main__":
    print("base:", BASE, "| armed:", armed(), "| notional:", NOTIONAL_USDT,
          "| leverage:", LEVERAGE)
    print("probe:", json.dumps(probe(), indent=1))
