"""
runner.py — Unified live loop. Every 5m boundary, for every tracked symbol:
  1. Pull fresh Binance 5m (and 1m for zones) data.
  2. Evaluate every strategy in strategies.STRATEGIES and log to the DB:
       - binary  strategies -> a next-bar-close prediction (spot bps), and for
                  meanrev on BTC/ETH also a real Polymarket book snapshot + bet.
       - bracket strategies -> an SL/TP paper trade (entry/stop/target).
  3. Resolve anything whose outcome is now known.

Symbols: BTC/ETH (Polymarket-eligible) + majors (spot-paper only). Timeframe:
5m only. meanrev is the one LIVE candidate; everything else is paper / data
collection — honest status lives in strategies.STRATEGIES and the dashboard.

Connectivity: needs api.binance.com (geo-blocked from US — worker runs in
Frankfurt). `python runner.py --probe` for a one-shot check.

Usage:
  python runner.py            # live loop
  python runner.py --probe    # one-shot connectivity + signal check
  python runner.py --once     # single cycle (cron-style)
"""
import json
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

import db
import strategies as S
import paper_trader as pt   # klines(), find_market(), best_book(), taker_fill(), fee_fraction(), candle_outcome()

# Polymarket has 5m Up/Down markets only for BTC/ETH -> those get real bets.
POLY = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
# Majors: spot-paper only (no Polymarket market). Add/remove here.
MAJORS = {"sol": "SOLUSDT", "xrp": "XRPUSDT", "doge": "DOGEUSDT", "bnb": "BNBUSDT"}
SYMBOLS = {**POLY, **MAJORS}

SIZE_USD = 100.0
WIN = 300                       # 5m in seconds
BRACKET_MAXBARS = 24           # 2h cap for SL/TP traversal resolution

_BINANCE_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision",
                  "https://api1.binance.com", "https://api2.binance.com"]


def _klines(symbol, interval, limit):
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


def _klines_from(symbol, start_ms, limit):
    """5m candles starting at start_ms — used to resolve a bracket trade against
    ITS OWN holding window (fetching the last N bars mis-resolves old trades
    whose post-entry bars have scrolled out of view)."""
    last_err = None
    for host in _BINANCE_HOSTS:
        try:
            raw = json.loads(urllib.request.urlopen(
                f"{host}/api/v3/klines?symbol={symbol}&interval=5m"
                f"&startTime={start_ms}&limit={limit}", timeout=15).read())
            df = pd.DataFrame([r[:6] for r in raw],
                              columns=["ts", "open", "high", "low", "close", "volume"])
            for k in df.columns[1:]:
                df[k] = df[k].astype(float)
            df["ts"] = df["ts"].astype("int64")
            return df
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"klines_from failed: {last_err}")


def _one_candle(symbol, start_ms):
    """Return (open, high, low, close) for the 5m candle at start_ms."""
    for host in _BINANCE_HOSTS:
        try:
            raw = json.loads(urllib.request.urlopen(
                f"{host}/api/v3/klines?symbol={symbol}&interval=5m"
                f"&startTime={start_ms}&limit=1", timeout=15).read())
            r = raw[0]
            return float(r[1]), float(r[2]), float(r[3]), float(r[4])
        except Exception:
            continue
    raise RuntimeError("candle fetch failed")


# --------------------------- recording helpers ---------------------------
def _record_binary(strat, symbol, dirn, rule, boundary, entry, detail=None):
    """A next-5m-close prediction, stored in trades with stop/target NULL and
    ts=boundary (the window it predicts). dirn: +1 up / -1 down."""
    sid = db.record_signal(strat, symbol, "5m", dirn, rule, detail=detail or {})
    db.record_trade(sid, symbol, "long" if dirn > 0 else "short",
                    round(entry, 6), None, None, ts=boundary)


def _record_bracket(strat, symbol, tr, detail=None):
    sid = db.record_signal(strat, symbol, "5m", tr["direction"],
                           tr.get("rule", strat), detail=detail or {})
    entry = float(tr["entry"])
    db.record_trade(sid, symbol, "long" if tr["direction"] > 0 else "short",
                    round(entry, 6), round(float(tr["stop"]), 6),
                    round(float(tr["target"]), 6))


def _maybe_bet(coin, symbol, side, rule, boundary, probe):
    """meanrev on BTC/ETH -> snapshot Polymarket book and log a real bet."""
    mkt, slug = pt.find_market(coin, boundary)
    if not mkt:
        if probe:
            print(f"  [{coin}] polymarket market not found: {slug}")
        return
    token = mkt.get(side)
    bids, asks = pt.best_book(token)
    fill, depth = pt.taker_fill(asks, SIZE_USD)
    if probe:
        print(f"  [{coin}] {slug} ask={asks[0][0] if asks else None} depth={depth}")
        return
    if not fill:
        return
    sid = db.record_signal("meanrev", symbol, "5m",
                           1 if side == "Up" else -1, rule,
                           detail={"slug": slug, "depth": depth})
    db.record_bet(sid, symbol, side, bids[0][0] if bids else None,
                  asks[0][0] if asks else None, round(fill, 4),
                  round(pt.fee_fraction(fill), 5), boundary)
    print(f"  BET {coin} {side} @ {fill:.3f} ({rule})")


# ------------------------------- cycle -----------------------------------
def cycle(probe=False):
    boundary = (int(time.time()) // WIN) * WIN
    for coin, symbol in SYMBOLS.items():
        try:
            df5 = _klines(symbol, "5m", 300)
            df1 = _klines(symbol, "1m", 1500)
        except Exception as e:
            print(f"[{coin}] binance error: {e}"); continue
        df5 = df5[df5.ts < boundary * 1000].reset_index(drop=True)   # closed bars only
        if len(df5) < 200:
            continue
        entry = float(df5["close"].iloc[-1])
        if probe:
            print(f"[{coin}] {symbol} bars={len(df5)} close={entry}")

        # ---- binary: meanrev (spot for all; Polymarket bet for BTC/ETH) ----
        side, rule = S.meanrev_signal(df5)
        if side:
            dirn = 1 if side == "Up" else -1
            if not probe:
                _record_binary("meanrev_spot", symbol, dirn, rule, boundary, entry)
            if coin in POLY:
                _maybe_bet(coin, symbol, side, rule, boundary, probe)
            if probe:
                print(f"  meanrev={side} ({rule})")

        # ---- binary: wick_fade ----
        d, r = S.wick_fade_signal(df5)
        if d and not probe:
            _record_binary("wick_fade", symbol, d, r, boundary, entry)
        if probe and d:
            print(f"  wick_fade={'Up' if d>0 else 'Down'} ({r})")

        # ---- binary: zone_break_bias ----
        d, r = S.zone_break_bias_signal(df5, df1)
        if d and not probe:
            _record_binary("zone_break_bias", symbol, d, r, boundary, entry)
        if probe and d:
            print(f"  zone_break_bias={'Up' if d>0 else 'Down'} ({r})")

        # ---- bracket family: gap zones (shared) ----
        bands = S.current_zone_bands(df5.tail(200).reset_index(drop=True),
                                     df1.tail(1500).reset_index(drop=True))
        if bands:
            brackets = {
                "gaptrav": S.gaptrav_open(df5, bands),
                "gaptrav_tight": S.gaptrav_tight_open(df5, bands),
                "meanrev_confluence": S.meanrev_confluence_open(df5, bands),
                "far_targets": S.far_targets_open(df5, bands),
            }
            for strat, tr in brackets.items():
                if not tr:
                    continue
                tr = dict(tr); tr["entry"] = entry
                # validity: target must be ahead and stop behind the entry
                if (tr["target"] - entry) * tr["direction"] <= 0:
                    continue
                if (entry - tr["stop"]) * tr["direction"] <= 0:
                    continue
                if probe:
                    print(f"  {strat}={'long' if tr['direction']>0 else 'short'} "
                          f"tgt={tr['target']:.4g} stop={tr['stop']:.4g}")
                else:
                    _record_bracket(strat, symbol, tr, detail={"gap": tr.get("gap")})
        elif probe:
            print("  (no zone bands)")


# ------------------------------ resolve ----------------------------------
def resolve():
    bets, trades = db.open_positions()
    now_ms = time.time() * 1000

    for b in bets:                                  # Polymarket binary bets
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

    for t in trades:
        if t["target"] is None:                     # binary next-bar prediction
            boundary = int(t["ts"])
            if now_ms < (boundary + WIN) * 1000 + 8000:
                continue
            try:
                o, _h, _l, c = _one_candle(t["symbol"], boundary * 1000)
            except Exception:
                continue
            d = 1 if t["side"] == "long" else -1
            up = c >= o
            won = int((up and d > 0) or (not up and d < 0))
            ret = d * (c - o) / o * 1e4
            db.resolve_trade(t["id"], round(c, 6), "up" if up else "down",
                             won, round(ret, 1), 1)
            print(f"  resolved {t['symbol']} {t['side']} (binary) -> "
                  f"{'up' if up else 'down'} {ret:+.0f}bps")
            continue

        # bracket SL/TP traversal — resolve against THIS trade's own window
        try:
            raw = _klines_from(t["symbol"], int(t["ts"]) * 1000, BRACKET_MAXBARS + 5)
        except Exception:
            continue
        seg = raw[raw.ts > t["ts"] * 1000].head(BRACKET_MAXBARS).reset_index(drop=True)
        if len(seg) < 1:
            continue
        d = 1 if t["side"] == "long" else -1
        outcome = exit_p = None; held = len(seg)
        for i, row in seg.iterrows():
            stop_hit = (row.close <= t["stop"]) if d > 0 else (row.close >= t["stop"])
            tgt_hit = (row.high >= t["target"]) if d > 0 else (row.low <= t["target"])
            if stop_hit:
                outcome, exit_p, held = "stop", t["stop"], i + 1; break
            if tgt_hit:
                outcome, exit_p, held = "target", t["target"], i + 1; break
        if outcome is None:
            # neither border hit; only time out once the FULL window has elapsed
            if now_ms < (int(t["ts"]) + BRACKET_MAXBARS * WIN) * 1000:
                continue
            outcome, exit_p = "timeout", float(seg["close"].iloc[-1])
        ret = d * (exit_p - t["entry"]) / t["entry"] * 1e4
        db.resolve_trade(t["id"], round(exit_p, 6), outcome,
                         int(outcome == "target"), round(ret, 1), held)
        print(f"  resolved TRADE {t['symbol']} {t['side']} -> {outcome} {ret:+.0f}bps")


if __name__ == "__main__":
    db.init()
    if "--probe" in sys.argv:
        cycle(probe=True); sys.exit()
    if "--once" in sys.argv:
        cycle(); resolve(); sys.exit()
    target = "Postgres/Neon" if db.IS_PG else db.DB_PATH
    print(f"runner live | {len(S.STRATEGIES)} strategies x {len(SYMBOLS)} symbols "
          f"(5m) -> {target}")
    while True:
        now = time.time()
        nxt = (int(now) // WIN + 1) * WIN
        time.sleep(max(0, nxt - now) + 2.0)
        try:
            cycle(); resolve()
        except Exception as e:
            print(f"cycle error: {e}")
