"""ideas_exec.py — P3 demo execution for TradingView Ideas (price-tracked bracket).

Turns `extracted` ideas (a chart-read bracket: direction + entry/target/stop) into
tracked demo trades and resolves them against REAL Binance price data:

  route   symbol (BTCUSD / BTCUSDT / ETHUSD …) → a Binance USDT pair, or None
          (unsupported markets are recorded `no_venue`, never executed).
  open    market-enter at the live Binance mid NOW (the plan's realtime intent:
          trade the idea within seconds), keep the idea's target/stop as the
          bracket. status extracted → open, with exec_entry + exec_ts.
  resolve walk the 1m klines over [exec_ts, now] in order; the FIRST bar that
          touches the stop → loss, that touches the target → win. A bar whose
          range straddles BOTH is scored a LOSS (pessimistic — never inflate, per
          the lab's anti-lookahead discipline). After the TF's max-hold bars with
          neither hit → flatten at the last close (outcome 'flat').

Honest labelling: venue is `binance_sim` — REAL public Binance prices, deterministic
no-lookahead TP/SL resolution, but NOT a broker fill (spot testnet is long-only and
region-blocks signed orders; shorts need the futures testnet from Frankfurt — the
documented next step). Long AND short both work here. Paper/demo only; the money
floor (LIVE_BUDGET_ARMED) is untouched — nothing here can place real-money orders.

Usage:
  python ideas_exec.py            # one cycle: open new extracted + resolve open
  python ideas_exec.py --probe    # show what it WOULD do, no DB writes
  python ideas_exec.py --open     # only open new extracted ideas
  python ideas_exec.py --resolve  # only resolve open idea-trades
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


# ─── Open: market-enter extracted ideas ──────────────────────────────────────
def open_extracted(probe=False):
    rows = db._rows("SELECT * FROM ideas WHERE status='extracted'")
    n_open = n_noven = 0
    for r in rows:
        bsym = route(r["symbol"])
        if not bsym:
            print(f"  idea {r['id']} {r['symbol']}: no demo venue -> no_venue")
            if not probe:
                _update(r["id"], status="no_venue")
            n_noven += 1
            continue
        if r["direction"] not in (1, -1) or not (r["target"] and r["stop"]):
            print(f"  idea {r['id']} {r['symbol']}: incomplete bracket -> skip")
            continue
        mid = live_mid(bsym)
        if mid is None:
            continue
        # geometry sanity: the market may have moved past the author's level.
        # target must still be ahead and stop behind, in the trade's direction;
        # if not, the drawn setup can no longer be entered -> invalidated (so it
        # doesn't linger as 'extracted' and get retried every run).
        d = r["direction"]
        if (r["target"] - mid) * d <= 0 or (mid - r["stop"]) * d <= 0:
            print(f"  idea {r['id']} {bsym}: setup invalidated — live {mid:.2f} "
                  f"past the level (tp={r['target']} sl={r['stop']} dir={d})")
            if not probe:
                _update(r["id"], status="invalidated")
            continue
        side = "LONG" if d == 1 else "SHORT"
        print(f"  idea {r['id']} {bsym}: OPEN {side} @ {mid:.2f} "
              f"tp={r['target']} sl={r['stop']} tf={r['timeframe']}")
        if not probe:
            _update(r["id"], status="open", venue=VENUE,
                    exec_entry=round(mid, 2), exec_ts=int(time.time()))
        n_open += 1
    return n_open, n_noven


# ─── Resolve: walk klines for the first TP/SL touch ───────────────────────────
def _resolve_one(r, now_ms):
    bsym = route(r["symbol"])
    entry = r["exec_entry"]
    if not bsym or not entry:
        return None
    d = float(r["direction"])
    tp = float(r["target"])
    sl = float(r["stop"])
    start_ms = int(r["exec_ts"]) * 1000
    bars = klines_1m(bsym, start_ms, now_ms)
    if not bars:
        return None
    tf_m = TF_MIN.get(r["timeframe"], 60)
    max_hold_ms = tf_m * MAX_HOLD_BARS * 60_000

    for k in bars:
        hi, lo = float(k[2]), float(k[3])
        hit_tp = (hi >= tp) if d > 0 else (lo <= tp)
        hit_sl = (lo <= sl) if d > 0 else (hi >= sl)
        if hit_tp and hit_sl:
            outcome, exitp = "stop", sl          # ambiguous bar -> pessimistic
        elif hit_sl:
            outcome, exitp = "stop", sl
        elif hit_tp:
            outcome, exitp = "target", tp
        else:
            continue
        ret = d * (exitp - entry) / entry * 1e4
        held = max(1, int((k[0] - start_ms) // (tf_m * 60_000)))
        return dict(outcome=outcome, won=int(outcome == "target"),
                    exit=round(exitp, 2), ret_bps=round(ret, 1), bars_held=held)

    # neither hit — flatten at last close once past the max hold
    last_close = float(bars[-1][4])
    if now_ms - start_ms >= max_hold_ms:
        ret = d * (last_close - entry) / entry * 1e4
        return dict(outcome="flat", won=int(ret > 0), exit=round(last_close, 2),
                    ret_bps=round(ret, 1), bars_held=MAX_HOLD_BARS)
    return None       # still open within the hold window


def resolve_open(probe=False):
    rows = db._rows("SELECT * FROM ideas WHERE status='open'")
    now_ms = int(time.time() * 1000)
    n = 0
    for r in rows:
        res = _resolve_one(r, now_ms)
        if not res:
            print(f"  idea {r['id']} {r['symbol']}: still open")
            continue
        sign = "+" if res["ret_bps"] >= 0 else ""
        print(f"  idea {r['id']} {r['symbol']}: {res['outcome'].upper()} "
              f"@ {res['exit']} ({sign}{res['ret_bps']} bps, {res['bars_held']} bars)")
        if not probe:
            _update(r["id"], status="resolved", outcome=res["outcome"],
                    ret_bps=res["ret_bps"], bars_held=res["bars_held"])
        n += 1
    return n


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="dry run, no DB writes")
    ap.add_argument("--open", action="store_true", help="only open new ideas")
    ap.add_argument("--resolve", action="store_true", help="only resolve open ideas")
    args = ap.parse_args()

    db.init()
    _ensure_cols()

    do_open = args.open or not args.resolve
    do_res  = args.resolve or not args.open

    if do_res:
        print("[ideas_exec] resolving open idea-trades…")
        nr = resolve_open(probe=args.probe)
        print(f"[ideas_exec] resolved {nr}")
    if do_open:
        print("[ideas_exec] opening extracted ideas…")
        no, nv = open_extracted(probe=args.probe)
        print(f"[ideas_exec] opened {no}, no_venue {nv}")
    if args.probe:
        print("[ideas_exec] (probe — no writes)")


if __name__ == "__main__":
    main()
