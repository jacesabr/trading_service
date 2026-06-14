"""
runner.py — Unified live loop. Every 5m boundary:
  1. Pull fresh Binance 5m (and 1m for zones) data.
  2. meanrev: evaluate -> if signal, snapshot Polymarket book, log a BET to DB.
  3. gaptrav: if just-closed bar closed in a gap, log a forex TRADE (paper) to DB.
  4. Resolve any open bets/trades whose outcome is now known.

Both strategies write to the same SQLite DB (db.py); the dashboard reads it.
meanrev = LIVE candidate (Polymarket). gaptrav = paper experiment.

Connectivity: needs api.binance.com (works from IN; geo-blocked from the build
container). Run `python3 runner.py --probe` first. Polymarket book + market
discovery reuse paper_trader.py helpers (verified working).

Usage:
  python3 runner.py            # live loop
  python3 runner.py --probe    # one-shot connectivity check
  python3 runner.py --once     # single cycle (for cron-style runs)
"""
import sys
import time
import numpy as np
import pandas as pd

import db
from strategies import meanrev_signal, gaptrav_open, current_zone_bands
import paper_trader as pt   # reuse klines(), find_market(), best_book(), taker_fill(), fee_fraction()

COINS = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
SIZE_USD = 100.0
GAP_FX_UNITS = 1.0
WIN = 300


def fetch(symbol, interval, limit):
    return pt.klines.__wrapped__(symbol, limit) if hasattr(pt.klines, "__wrapped__") \
        else _klines(symbol, interval, limit)


_BINANCE_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision",
                  "https://api1.binance.com", "https://api2.binance.com"]


def _klines(symbol, interval, limit):
    import json, urllib.request
    last_err = None
    for host in _BINANCE_HOSTS:
        try:
            raw = json.loads(urllib.request.urlopen(
                f"{host}/api/v3/klines?symbol={symbol}"
                f"&interval={interval}&limit={limit}", timeout=15).read())
            df = pd.DataFrame([r[:6] for r in raw],
                              columns=["ts", "open", "high", "low", "close", "volume"])
            for k in df.columns[1:]:
                df[k] = df[k].astype(float)
            df["ts"] = df["ts"].astype("int64")
            return df
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"all Binance hosts failed (geo-block?): {last_err}")


def cycle(probe=False):
    boundary = (int(time.time()) // WIN) * WIN
    for coin, symbol in COINS.items():
        try:
            df5 = _klines(symbol, "5m", 300)
            df1 = _klines(symbol, "1m", 1500)
        except Exception as e:
            print(f"[{coin}] binance error: {e}"); continue
        df5 = df5[df5.ts < boundary * 1000]          # only closed bars
        if len(df5) < 200:
            continue

        # ---- Strategy A: meanrev -> Polymarket bet ----
        side, rule = meanrev_signal(df5)
        if probe:
            print(f"[{coin}] meanrev={side or 'none'}", end="  ")
        if side and not probe:
            mkt, slug = pt.find_market(coin, boundary)
            if mkt:
                token = mkt.get(side)
                bids, asks = pt.best_book(token)
                fill, depth = pt.taker_fill(asks, SIZE_USD)
                if fill:
                    sid = db.record_signal("meanrev", symbol, "5m",
                                           1 if side == "Up" else -1, rule,
                                           detail={"slug": slug, "depth": depth})
                    db.record_bet(sid, symbol, side,
                                  bids[0][0] if bids else None,
                                  asks[0][0] if asks else None, round(fill, 4),
                                  round(pt.fee_fraction(fill), 5), boundary)
                    print(f"BET {coin} {side} @ {fill:.3f} ({rule})")

        # ---- Strategy B: gaptrav -> forex paper trade ----
        bands = current_zone_bands(df5.tail(200).reset_index(drop=True),
                                   df1.tail(1500).reset_index(drop=True))
        if bands:
            tr = gaptrav_open(df5, bands)
            if probe:
                print(f"gaptrav={'gap-close' if tr else 'none'}")
            if tr and not probe:
                entry = df5["close"].iloc[-1]        # paper: approximate next open
                sid = db.record_signal("gaptrav", symbol, "5m", tr["direction"],
                                       "gap_close", detail={"gap": tr["gap"]})
                db.record_trade(sid, symbol,
                                "long" if tr["direction"] > 0 else "short",
                                round(entry, 2), round(tr["stop"], 2),
                                round(tr["target"], 2))
                print(f"TRADE {coin} {'long' if tr['direction']>0 else 'short'} "
                      f"tgt={tr['target']:.0f} stop={tr['stop']:.0f}")
        elif probe:
            print()


def resolve():
    bets, trades = db.open_positions()
    now_ms = time.time() * 1000
    for b in bets:
        end = (b["window_start"] + WIN) * 1000
        if now_ms < end + 8000:
            continue
        try:
            out = pt.candle_outcome(b["symbol"], b["window_start"] * 1000)
        except Exception:
            continue
        won = int(out == b["side"])
        px = b["entry_price"]; shares = SIZE_USD / px
        fee = SIZE_USD * b["fee_frac"]
        pnl = round(shares * won - SIZE_USD - fee, 2)
        db.resolve_bet(b["id"], out, won, pnl)
        print(f"  resolved BET {b['symbol']} {b['side']} -> {out} pnl={pnl}")
    # forex trades: resolve from 5m candles after entry (touch target / close stop)
    for t in trades:
        try:
            df = _klines(t["symbol"], "5m", 60)
        except Exception:
            continue
        entry_ms = t["ts"] * 1000
        seg = df[df.ts > entry_ms]
        if len(seg) < 1:
            continue
        d = 1 if t["side"] == "long" else -1
        outcome = None; exit_p = None
        for _, row in seg.iterrows():
            stop_hit = (row.close <= t["stop"]) if d > 0 else (row.close >= t["stop"])
            tgt_hit = (row.high >= t["target"]) if d > 0 else (row.low <= t["target"])
            if stop_hit:
                outcome, exit_p = "stop", t["stop"]; break
            if tgt_hit:
                outcome, exit_p = "target", t["target"]; break
        if outcome:
            ret = d * (exit_p - t["entry"]) / t["entry"] * 1e4
            db.resolve_trade(t["id"], exit_p, outcome, int(outcome == "target"),
                             round(ret, 1), len(seg))
            print(f"  resolved TRADE {t['symbol']} {t['side']} -> {outcome} {ret:+.0f}bps")


if __name__ == "__main__":
    db.init()
    if "--probe" in sys.argv:
        cycle(probe=True); sys.exit()
    if "--once" in sys.argv:
        cycle(); resolve(); sys.exit()
    target = "Postgres/Neon" if db.IS_PG else db.DB_PATH
    print(f"runner live | meanrev(Polymarket) + gaptrav(forex paper) -> {target}")
    while True:
        now = time.time()
        nxt = (int(now) // WIN + 1) * WIN
        time.sleep(max(0, nxt - now) + 2.0)
        try:
            cycle(); resolve()
        except Exception as e:
            print(f"cycle error: {e}")
