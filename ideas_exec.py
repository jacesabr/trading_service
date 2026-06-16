"""ideas_exec.py — P3 demo execution for TradingView Ideas (limit-entry bracket).

Turns `extracted` ideas (a chart-read bracket: direction + entry/target/stop) into
tracked demo trades and resolves them against REAL Binance price data. Entries are
LIMIT/STOP orders at the author's entry level — NOT a market-now fill — so an idea
isn't thrown away just because the market hasn't reached its entry yet:

  route    symbol (BTCUSD / BTCUSDT / ETHUSD …) → a Binance USDT pair, or None
           (unsupported markets are recorded `no_venue`, never executed).
  work     extracted → `pending`: a resting order at the entry level. Only the
           bracket GEOMETRY is checked (target ahead, stop behind the ENTRY, in
           the trade's direction); a bad bracket → `invalidated`. Price location
           does NOT reject it — that's what the resting order is for.
  fill     `pending` → `open`: walk 1m klines from when we first saw the idea; the
           first bar whose range touches the entry level fills the order there
           (works as a limit for pullback entries and a stop for breakouts). If the
           entry is never reached within the max-wait → `expired`.
  resolve  `open` → `resolved`: from the fill bar onward, the first bar to touch
           the stop → loss, the target → win; a bar straddling BOTH is a LOSS
           (pessimistic — never inflate, per the lab's no-lookahead discipline).
           Past the TF's max-hold with neither hit → flatten at the last close
           (`flat`). Long AND short both resolve correctly.

No-lookahead: klines are only ever read from the bar AFTER we recorded the idea, so
a freshly-scraped idea whose entry/target already played out resolves on real,
already-closed bars — never on information from before we knew about it.

Honest labelling: venue is `binance_sim` — REAL public Binance prices + deterministic
resolution, but NOT a broker fill (spot testnet is long-only + region-blocks signed
orders; real short fills need the futures testnet / Kraken paper-futures — next).
Paper/demo only; the money floor (LIVE_BUDGET_ARMED) is untouched.

Usage:
  python ideas_exec.py            # one cycle: work extracted→pending + fill/resolve
  python ideas_exec.py --probe    # show what it WOULD do, no DB writes
  python ideas_exec.py --open     # only move extracted → pending (place orders)
  python ideas_exec.py --resolve  # only fill pending + resolve open
"""
import argparse
import json
import os
import time
import urllib.request

import db

BINANCE = os.environ.get("BINANCE_REST", "https://api.binance.com")
VENUE   = "binance_sim"

# TF → minutes; sets the max-hold (after which an un-hit bracket is flattened) so
# slots free up. Timeframe-agnostic: any TF is tradeable, the TF just scales the
# hold (a 1d idea holds days, a 5m idea minutes-to-hours).
TF_MIN  = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
           "1h": 60, "2h": 120, "3h": 180, "4h": 240, "6h": 360, "8h": 480,
           "12h": 720, "1d": 1440, "3d": 4320, "1w": 10080}
MAX_HOLD_BARS = 24            # e.g. a 1h idea holds ≤24h, a 1d idea ≤24 days
MAX_WAIT_BARS = 12            # un-filled resting order expires after this many TF bars


# ─── Binance public data (keyless; works from any region) ─────────────────────
def _get(url, timeout=20):
    return json.loads(urllib.request.urlopen(url, timeout=timeout).read())


def live_mid(binance_sym):
    """Live mid price from the public book ticker, or None."""
    try:
        b = _get(f"{BINANCE}/api/v3/ticker/bookTicker?symbol={binance_sym}")
        return (float(b["bidPrice"]) + float(b["askPrice"])) / 2.0
    except Exception as e:
        print(f"  [exec] live_mid {binance_sym} failed: {e}")
        return None


def klines_1m(binance_sym, start_ms, end_ms):
    """1m OHLC over [start_ms, end_ms]. Paginates (1000 bars/call). Each row:
    [openTime, open, high, low, close, ...]."""
    out, cur = [], int(start_ms)
    while cur < end_ms:
        url = (f"{BINANCE}/api/v3/klines?symbol={binance_sym}&interval=1m"
               f"&startTime={cur}&endTime={int(end_ms)}&limit=1000")
        try:
            batch = _get(url)
        except Exception as e:
            print(f"  [exec] klines {binance_sym} failed: {e}")
            break
        if not batch:
            break
        out.extend(batch)
        nxt = batch[-1][0] + 60_000
        if nxt <= cur:
            break
        cur = nxt
        if len(batch) < 1000:
            break
    return out


# ─── Router ───────────────────────────────────────────────────────────────────
_BASE_ALIASES = {"XBT": "BTC"}


def route(symbol):
    """TradingView symbol → a Binance USDT spot pair, or None if unsupported.

    Handles BTCUSDT / BTCUSD / BTC / ETHUSD … by extracting the base asset and
    pinning the quote to USDT (the liquid Binance pair). Non-crypto / unknown
    bases return None → recorded `no_venue`."""
    if not symbol:
        return None
    s = symbol.upper().strip()
    # strip a leading EXCHANGE: prefix if present (e.g. BINANCE:BTCUSDT)
    if ":" in s:
        s = s.split(":", 1)[1]
    for q in ("USDT", "USD", "USDC", "PERP"):
        if s.endswith(q):
            s = s[: -len(q)]
            break
    s = _BASE_ALIASES.get(s, s)
    # supported liquid crypto bases (demo). Extend as venues grow.
    supported = {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX",
                 "LINK", "MATIC", "LTC", "DOT", "TRX"}
    if s in supported:
        return s + "USDT"
    return None


# ─── DB helpers (idea exec columns) ───────────────────────────────────────────
def _ensure_cols():
    """Add the execution columns to `ideas` if missing (idempotent)."""
    have = set()
    if db.IS_PG:
        for r in db._rows("SELECT column_name FROM information_schema.columns "
                          "WHERE table_name='ideas'"):
            have.add(r["column_name"])
    else:
        for r in db._rows("PRAGMA table_info(ideas)"):
            have.add(r["name"])
    add = {"venue": "TEXT", "exec_entry": "DOUBLE PRECISION",
           "exec_ts": "BIGINT", "bars_held": "INTEGER"}
    if not db.IS_PG:
        add = {k: ("REAL" if "DOUBLE" in v else "INTEGER" if v == "BIGINT" else v)
               for k, v in add.items()}
    c = db.conn(); cur = c.cursor()
    for col, typ in add.items():
        if col not in have:
            try:
                cur.execute(f"ALTER TABLE ideas ADD COLUMN {col} {typ}")
            except Exception:
                pass
    c.commit(); cur.close(); c.close()


def _update(idea_id, **cols):
    sets = ",".join(f"{k}={db.PH}" for k in cols)
    vals = list(cols.values()) + [idea_id]
    c = db.conn(); cur = c.cursor()
    cur.execute(f"UPDATE ideas SET {sets} WHERE id={db.PH}", vals)
    c.commit(); cur.close(); c.close()


# ─── Work: place a resting limit/stop order at the entry ──────────────────────
def work_orders(probe=False):
    """extracted → pending (a resting order at the author's entry). Rejects only
    on routing (no_venue), an incomplete or geometrically-bad bracket — NOT on
    where the live price is (that's what the resting order waits for)."""
    rows = db._rows("SELECT * FROM ideas WHERE status='extracted'")
    n_work = n_noven = n_bad = 0
    for r in rows:
        bsym = route(r["symbol"])
        if not bsym:
            print(f"  idea {r['id']} {r['symbol']}: no demo venue -> no_venue")
            if not probe:
                _update(r["id"], status="no_venue")
            n_noven += 1
            continue
        d = r["direction"]
        if d not in (1, -1) or not (r["entry"] and r["target"] and r["stop"]):
            print(f"  idea {r['id']} {r['symbol']}: incomplete bracket "
                  f"(entry/target/stop/dir) -> needs_vision")
            if not probe:
                _update(r["id"], status="needs_vision")
            continue
        # geometry on the ENTRY (price location is irrelevant for a resting order):
        # target ahead of entry, stop behind it, in the trade's direction.
        if (r["target"] - r["entry"]) * d <= 0 or (r["entry"] - r["stop"]) * d <= 0:
            print(f"  idea {r['id']} {bsym}: bad bracket geometry "
                  f"(entry={r['entry']} tp={r['target']} sl={r['stop']} dir={d}) "
                  f"-> invalidated")
            if not probe:
                _update(r["id"], status="invalidated")
            n_bad += 1
            continue
        side = "LONG" if d == 1 else "SHORT"
        print(f"  idea {r['id']} {bsym}: ORDER {side} entry={r['entry']} "
              f"tp={r['target']} sl={r['stop']} tf={r['timeframe']} -> pending")
        if not probe:
            _update(r["id"], status="pending", venue=VENUE)
        n_work += 1
    return n_work, n_noven, n_bad


# ─── Fill + resolve: walk klines from when we first saw the idea ──────────────
def _evaluate(r, now_ms):
    """Walk 1m klines for a pending/open idea → fill at the entry level, then the
    first TP/SL touch. Returns a dict of column updates, or None if no change."""
    bsym = route(r["symbol"])
    if not bsym:
        return None
    d   = float(r["direction"])
    ent = float(r["entry"])
    tp  = float(r["target"])
    sl  = float(r["stop"])
    tf_m = TF_MIN.get(r["timeframe"], 60)

    already_open = r["status"] == "open" and r.get("exec_ts")
    start_ms = int(r["exec_ts"]) * 1000 if already_open else int(r["ts"]) * 1000
    bars = klines_1m(bsym, start_ms, now_ms)
    if not bars:
        return None

    out = {}
    fill_ms = int(r["exec_ts"]) * 1000 if already_open else None

    # 1) fill the resting order at the entry level (first bar that trades through it)
    bracket_bars = bars
    if not already_open:
        wait_ms = tf_m * MAX_WAIT_BARS * 60_000
        for i, k in enumerate(bars):
            hi, lo = float(k[2]), float(k[3])
            if lo <= ent <= hi:                       # price reached the entry
                fill_ms = k[0]
                out.update(status="open", exec_entry=round(ent, 2),
                           exec_ts=int(fill_ms // 1000))
                bracket_bars = bars[i:]
                break
        else:
            # never reached entry: expire if the wait window has elapsed
            if now_ms - start_ms >= wait_ms:
                return {"status": "expired"}
            return None                                # still resting

    # 2) from the fill bar onward, first TP/SL touch (ambiguous bar = loss)
    max_hold_ms = tf_m * MAX_HOLD_BARS * 60_000
    for k in bracket_bars:
        hi, lo = float(k[2]), float(k[3])
        hit_tp = (hi >= tp) if d > 0 else (lo <= tp)
        hit_sl = (lo <= sl) if d > 0 else (hi >= sl)
        if hit_tp and hit_sl:
            outcome, exitp = "stop", sl
        elif hit_sl:
            outcome, exitp = "stop", sl
        elif hit_tp:
            outcome, exitp = "target", tp
        else:
            continue
        ret = d * (exitp - ent) / ent * 1e4
        held = max(1, int((k[0] - fill_ms) // (tf_m * 60_000)))
        out.update(status="resolved", outcome=outcome,
                   ret_bps=round(ret, 1), bars_held=held)
        return out

    # neither hit — flatten at the last close past the max hold
    if fill_ms is not None and now_ms - fill_ms >= max_hold_ms:
        last_close = float(bracket_bars[-1][4])
        ret = d * (last_close - ent) / ent * 1e4
        out.update(status="resolved", outcome="flat",
                   ret_bps=round(ret, 1), bars_held=MAX_HOLD_BARS)
        return out

    return out or None                                # 'open' if just filled, else no change


def resolve_open(probe=False):
    """Fill pending orders + resolve open ones. (Name kept for daily.py.)"""
    rows = db._rows("SELECT * FROM ideas WHERE status IN ('pending','open')")
    now_ms = int(time.time() * 1000)
    n_filled = n_resolved = n_expired = 0
    for r in rows:
        res = _evaluate(r, now_ms)
        if not res:
            print(f"  idea {r['id']} {r['symbol']}: {r['status']} (no change)")
            continue
        st = res.get("status")
        if st == "expired":
            print(f"  idea {r['id']} {r['symbol']}: entry never reached -> expired")
            n_expired += 1
        elif st == "resolved":
            sign = "+" if res["ret_bps"] >= 0 else ""
            print(f"  idea {r['id']} {r['symbol']}: {res['outcome'].upper()} "
                  f"({sign}{res['ret_bps']} bps, {res['bars_held']} bars)")
            n_resolved += 1
        elif st == "open":
            print(f"  idea {r['id']} {r['symbol']}: FILLED @ {res['exec_entry']} "
                  f"-> open")
            n_filled += 1
        if not probe:
            _update(r["id"], **res)
    return dict(filled=n_filled, resolved=n_resolved, expired=n_expired)


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="dry run, no DB writes")
    ap.add_argument("--open", action="store_true",
                    help="only place resting orders (extracted -> pending)")
    ap.add_argument("--resolve", action="store_true",
                    help="only fill pending + resolve open")
    args = ap.parse_args()

    db.init()
    _ensure_cols()

    do_open = args.open or not args.resolve
    do_res  = args.resolve or not args.open

    if do_open:
        print("[ideas_exec] placing resting orders (extracted -> pending)…")
        nw, nv, nb = work_orders(probe=args.probe)
        print(f"[ideas_exec] pending {nw}, no_venue {nv}, invalidated {nb}")
    if do_res:
        print("[ideas_exec] filling pending + resolving open…")
        rr = resolve_open(probe=args.probe)
        print(f"[ideas_exec] filled {rr['filled']}, resolved {rr['resolved']}, "
              f"expired {rr['expired']}")
    if args.probe:
        print("[ideas_exec] (probe — no writes)")


if __name__ == "__main__":
    main()
