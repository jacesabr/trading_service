"""equity_orders.py — REAL Alpaca paper EQUITY bracket orders (broker-managed OCO).

The most deterministic real-execution path in the lab: we submit ONE bracket order
(market entry + take-profit limit + stop-loss stop) and the BROKER holds and fills
the exit itself (the two legs are OCO — one fills, the other cancels). There is no
close leg of ours, so it cannot accumulate or mis-resolve: the venue owns the exit.
Recorded in `executions` with the real entry fill (parent) + real exit fill (the
filled leg) + real P&L. This replaces the optimistic intrabar-touch bracket SIM
(the audit's biggest inflation) with broker ground truth.

Gated: ALPACA_EQUITY_ORDERS=1 (+ keys). Allow-list ALPACA_EQUITY_STRATEGIES
(default 'gaptrav_tight_eq_1h'). qty ALPACA_EQUITY_QTY (default 1). Deduped by an
open execution per (strategy, symbol). Equities fill only in market hours; orders
placed while closed queue (pending_new) and fill at the next open. Money floor
untouched (paper account; real money still needs LIVE_BUDGET_ARMED).
"""
import json
import os
import urllib.error
import urllib.request

import db

PAPER = "https://paper-api.alpaca.markets/v2"
QTY = os.environ.get("ALPACA_EQUITY_QTY", "1")


def armed():
    return (os.environ.get("ALPACA_EQUITY_ORDERS") == "1"
            and bool(os.environ.get("ALPACA_KEY"))
            and bool(os.environ.get("ALPACA_SECRET")))


def allowed(strat):
    allow = os.environ.get("ALPACA_EQUITY_STRATEGIES",
                           "meanrev_eq_5m,wick_fade_eq_5m")
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
        return json.loads(raw) if raw else {}      # DELETE returns 204/empty
    except urllib.error.HTTPError as e:
        return {"err": e.code, "body": e.read().decode()[:200]}
    except Exception as e:
        return {"err": -1, "body": str(e)[:200]}


def has_open(strat, symbol):
    """True if an open bracket execution already exists for (strategy, symbol)."""
    rows = db._rows(
        "SELECT e.id FROM executions e JOIN signals s ON e.signal_id=s.id "
        f"WHERE e.venue='alpaca' AND e.outcome='' AND e.symbol={db.PH} "
        f"AND s.strategy={db.PH} AND e.ref LIKE {db.PH}",
        (symbol, strat, "bracket:%"))
    return bool(rows)


def place_bracket(strat, signal_id, symbol, direction, entry, stop, target,
                  qty=None):
    """Submit a REAL Alpaca paper bracket order and record the open execution.
    Returns exec_id or None. No-op unless armed + allow-listed + valid bracket
    geometry + not already open for (strategy, symbol)."""
    if not armed() or not allowed(strat):
        return None
    # geometry: target ahead, stop behind, in the trade's direction
    if (target - entry) * direction <= 0 or (entry - stop) * direction <= 0:
        return None
    if has_open(strat, symbol):
        return None
    side = "buy" if direction > 0 else "sell"
    body = {"symbol": symbol, "qty": str(qty or QTY), "side": side,
            "type": "market", "time_in_force": "day", "order_class": "bracket",
            "take_profit": {"limit_price": round(float(target), 2)},
            "stop_loss": {"stop_price": round(float(stop), 2)}}
    o = _api("/orders", "POST", body)
    if o.get("err") or not o.get("id"):
        print(f"  [equity_orders] bracket reject {symbol} {side}: {o.get('body', o)}")
        return None
    eid = db.record_execution(signal_id, "alpaca", symbol, side,
                              round(float(entry), 4), round(float(stop), 4),
                              round(float(target), 4), ref=f"bracket:{o['id']}")
    print(f"  [equity_orders] bracket {side} {symbol} qty={qty or QTY} "
          f"tp={round(float(target),2)} sl={round(float(stop),2)} "
          f"({o.get('status')}, exec {eid})")
    return eid


def resolve_open():
    """Reconcile open bracket executions from Alpaca. When the broker has filled
    one OCO leg (TP or SL), record the real exit + P&L. If the parent never filled
    and is terminally dead, void the row so it can't linger. Returns # resolved."""
    if not (os.environ.get("ALPACA_KEY") and os.environ.get("ALPACA_SECRET")):
        return 0
    rows = db._rows("SELECT * FROM executions WHERE venue='alpaca' AND "
                    f"outcome='' AND ref LIKE {db.PH}", ("bracket:%",))
    n = 0
    for e in rows:
        oid = (e.get("ref") or "")[len("bracket:"):]
        if not oid:
            continue
        o = _api(f"/orders/{oid}?nested=true")
        if o.get("err"):
            continue
        pstatus = o.get("status")
        legs = o.get("legs") or []
        filled = [l for l in legs if l.get("status") == "filled"
                  and l.get("filled_avg_price")]
        if filled:
            leg = filled[0]
            exitp = float(leg["filled_avg_price"])
            entry = float(o.get("filled_avg_price") or e["entry"])  # real entry
            d = 1 if e["side"] == "buy" else -1
            ret = d * (exitp - entry) / entry * 1e4
            outcome = "target" if leg.get("type") == "limit" else "stop"
            db.resolve_execution(e["id"], round(exitp, 4), outcome,
                                 int(outcome == "target"), round(ret, 1), 0)
            print(f"  [equity_orders] CLOSED {e['symbol']} {outcome} {ret:+.1f}bps "
                  f"(exec {e['id']})")
            n += 1
        elif pstatus in ("canceled", "expired", "rejected"):
            # parent never became a position -> void (no fill, no P&L)
            db.resolve_execution(e["id"], float(e["entry"] or 0), "void", 0, 0.0, 0)
            print(f"  [equity_orders] VOID {e['symbol']} ({pstatus}, exec {e['id']})")
            n += 1
        # else: still working / partially open -> wait for the broker
    return n


if __name__ == "__main__":
    print("armed:", armed(), "| allow-list:",
          os.environ.get("ALPACA_EQUITY_STRATEGIES",
                         "meanrev_eq_5m,wick_fade_eq_5m"),
          "| qty:", QTY)
