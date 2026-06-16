"""alpaca_exec.py — REAL Alpaca paper order execution (filled demo trades).

Turns selected paper signals into ACTUAL filled orders on the Alpaca paper
account, recorded in the `executions` table with the venue's real entry fill and
real exit P&L. Alpaca spot crypto cannot be shorted, so this does LONG-only
crypto round-trips: buy to open, sell to flatten on resolve — positions never
accumulate. Equity / short signals are left to the sim path.

Lifecycle per demo trade:
  entry  open_long(strat, signal_id, binance_symbol)
         -> market buy, poll for the real fill, record an open execution
            (venue='alpaca', ref='order:<id>', entry=real fill price)
  exit   close_due()  [called from runner.resolve every cycle]
         -> for each open alpaca order-exec past one bar, market-sell the held
            qty, record the real exit fill + real ret_bps, resolve the execution.

Safety:
  * Gated — does nothing unless ALPACA_PLACE_ORDERS=1 (and keys present).
  * Allow-list — only strategies in ALPACA_ORDER_STRATEGIES (default 'clv_fade').
  * Dedup — never opens a second order for a (strategy, symbol) already open.
  * Force-flatten — any order-exec older than ALPACA_MAX_HOLD_S is closed no
    matter what, so a worker restart between buy and sell can't leave a position
    dangling forever.
Money floor is untouched: this is the PAPER account ($100k demo), never real
money. Real money still requires LIVE_BUDGET_ARMED (separate, off).
"""
import json
import os
import time
import urllib.error
import urllib.request

import db

PAPER = "https://paper-api.alpaca.markets/v2"
SYMMAP = {"BTCUSDT": "BTC/USD", "ETHUSDT": "ETH/USD", "SOLUSDT": "SOL/USD",
          "DOGEUSDT": "DOGE/USD", "XRPUSDT": "XRP/USD"}
NOTIONAL = float(os.environ.get("ALPACA_ORDER_NOTIONAL", "12"))   # > $10 crypto floor
BAR_S = 300                                                       # 5m hold (binary)
MAX_HOLD_S = int(os.environ.get("ALPACA_MAX_HOLD_S", "1800"))     # 30m force-flatten
ENTRY_POLL_S = int(os.environ.get("ALPACA_ENTRY_POLL_S", "15"))   # fill wait at entry


def armed():
    return (os.environ.get("ALPACA_PLACE_ORDERS") == "1"
            and bool(os.environ.get("ALPACA_KEY"))
            and bool(os.environ.get("ALPACA_SECRET")))


def _allowed(strat):
    allow = os.environ.get("ALPACA_ORDER_STRATEGIES", "clv_fade")
    return strat in [a.strip() for a in allow.split(",") if a.strip()]


def _hdr():
    return {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"],
            "Content-Type": "application/json"}


def _api(path, method="GET", body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(PAPER + path, headers=_hdr(), data=data,
                                 method=method)
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode().strip()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"err": e.code, "body": e.read().decode()[:200]}
    except Exception as e:
        return {"err": -1, "body": str(e)[:200]}


def _await_fill(oid, poll=ENTRY_POLL_S):
    """Poll an order until it reports a filled average price (or terminal)."""
    o = {}
    for _ in range(max(1, poll)):
        o = _api(f"/orders/{oid}")
        if o.get("filled_avg_price"):
            return float(o["filled_avg_price"]), o.get("status")
        if o.get("status") in ("rejected", "canceled", "expired"):
            return None, o.get("status")
        time.sleep(1)
    return None, o.get("status", "pending")


def _open_order_exec(strat, binance_symbol):
    """True if an alpaca order-exec is already open for this (strategy, symbol)."""
    rows = db._rows(
        "SELECT e.id FROM executions e JOIN signals s ON e.signal_id=s.id "
        f"WHERE e.venue='alpaca' AND e.outcome='' AND e.symbol={db.PH} "
        f"AND s.strategy={db.PH} AND e.ref LIKE 'order:%'",
        (binance_symbol, strat))
    return bool(rows)


def open_long(strat, signal_id, binance_symbol, direction):
    """Place a REAL paper buy for a long signal and record the open execution.
    Returns exec_id or None. No-op unless armed + allow-listed + long + a
    supported crypto symbol + not already open for this (strategy, symbol)."""
    if not armed() or not _allowed(strat) or direction <= 0:
        return None
    sym = SYMMAP.get(binance_symbol)
    if not sym:
        return None
    if _open_order_exec(strat, binance_symbol):
        return None                                  # dedup: one open at a time
    o = _api("/orders", "POST", {"symbol": sym, "notional": str(NOTIONAL),
                                 "side": "buy", "type": "market",
                                 "time_in_force": "gtc"})
    if o.get("err") or not o.get("id"):
        print(f"  [alpaca_exec] buy reject {sym}: {o.get('body', o)}")
        return None
    fill, status = _await_fill(o["id"])
    if not fill:
        _api(f"/orders/{o['id']}", "DELETE")         # not filled in window: cancel
        print(f"  [alpaca_exec] buy {sym} not filled ({status}); cancelled")
        return None
    eid = db.record_execution(signal_id, "alpaca", binance_symbol, "long",
                              round(fill, 6), None, None, ref=f"order:{o['id']}")
    print(f"  [alpaca_exec] FILLED buy {sym} @ {fill} (exec {eid})")
    return eid


def _position_qty(sym, tries=1):
    """Held qty for sym, polling a few times so a just-filled position that
    hasn't settled into /positions yet is still found (avoids a false 'flat')."""
    enc = sym.replace("/", "")                       # positions key: BTC/USD -> BTCUSD
    for i in range(max(1, tries)):
        p = _api(f"/positions/{enc}")
        try:
            if p and not p.get("err"):
                q = float(p.get("qty"))
                if q != 0:
                    return q
        except (TypeError, ValueError):
            pass
        if i < tries - 1:
            time.sleep(1)
    return 0.0


def _hard_close(sym):
    """Backstop: close the entire position for sym (no qty math). Returns the
    real fill price of the closing order, or None."""
    enc = sym.replace("/", "")                       # positions key: BTC/USD -> BTCUSD
    o = _api(f"/positions/{enc}", "DELETE")
    if o.get("err") or not o.get("id"):
        return None
    fill, _ = _await_fill(o["id"])
    return fill


def close_due(now=None):
    """Flatten every open alpaca order-exec that has held >= one bar (or is past
    the force-flatten age), recording the real exit fill + ret_bps. Returns the
    number closed. Safe to call every cycle; no-op when not armed."""
    if not armed():
        return 0
    now = int(time.time()) if now is None else int(now)
    rows = db._rows("SELECT * FROM executions WHERE venue='alpaca' AND "
                    "outcome='' AND ref LIKE 'order:%'")
    n = 0
    for e in rows:
        age = now - int(e["ts"])
        if age < BAR_S:
            continue                                 # still within the hold bar
        binance_symbol = e["symbol"]
        sym = SYMMAP.get(binance_symbol)
        if not sym:
            continue
        qty = _position_qty(sym, tries=8)            # let a fresh fill settle
        exitp = None
        if qty > 0:
            o = _api("/orders", "POST", {"symbol": sym, "qty": str(qty),
                                         "side": "sell", "type": "market",
                                         "time_in_force": "gtc"})
            if not o.get("err") and o.get("id"):
                exitp, _ = _await_fill(o["id"])
            if exitp is None:                        # sell unconfirmed -> hard close
                exitp = _hard_close(sym)
        entry = float(e["entry"]) if e["entry"] else None
        if exitp and entry:
            ret = (exitp - entry) / entry * 1e4      # long bps
            won = int(ret > 0)
            outcome = "win" if won else "loss"
        else:
            # no position to close (already flat) — resolve so it can't loop.
            _hard_close(sym)                         # belt-and-suspenders
            exitp = entry or 0.0
            ret = 0.0
            won = 0
            outcome = "flat" if age < MAX_HOLD_S else "forced_flat"
        db.resolve_execution(e["id"], round(exitp, 6), outcome, won,
                             round(ret, 1), max(1, age // BAR_S))
        print(f"  [alpaca_exec] CLOSED {sym} {outcome} {ret:+.1f}bps (exec {e['id']})")
        n += 1
    return n


if __name__ == "__main__":
    print("armed:", armed(), "| allow-list:",
          os.environ.get("ALPACA_ORDER_STRATEGIES", "clv_fade"),
          "| notional:", NOTIONAL)
