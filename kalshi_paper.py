"""kalshi_paper.py — Kalshi crypto settlement model (paper, real-API verified).

Kalshi finalizes each crypto price-threshold market with a GROUND-TRUTH result
(status=finalized, result=yes/no). So this strategy needs no Kalshi liquidity and
places NO orders — it scores a vol model against Kalshi's REAL settlement:

  strategy `kalshi_crypto_model` (research / data-collection)
  for each near-money "above K" market closing within WINDOW_S:
    * live spot S (Binance, fallback Coinbase) + recent 5m realized vol σ
    * τ = time-to-close;  P(settle > K) = Φ( ln(S/K) / (σ·√τ) )   (driftless)
    * record the prediction as an execution (venue=kalshi, ref=kalshi:<ticker>)
    * settle from Kalshi's finalized result; ret_bps = ±100 (right/wrong score)

Verified-data: the outcome is Kalshi's REAL finalized settlement via the API, not
self-resolved. No orders placed (paper floor holds); a real-order path would be a
separate P5 step gated behind LIVE_BUDGET_ARMED.

  collect()       record model predictions on near-money markets
  resolve_open()  settle matured predictions from Kalshi results
"""
import json
import math
import os
import time
import urllib.request
from datetime import datetime

import db
from adapters.data import kalshi as kx

STRATEGY = "kalshi_crypto_model"
WINDOW_S = int(os.environ.get("KALSHI_WINDOW_S", str(8 * 3600)))   # markets ≤8h out
MAX_NEW_PER_RUN = int(os.environ.get("KALSHI_MAX_NEW", "20"))
P_LO, P_HI = 0.05, 0.95            # only record informative (non-degenerate) calls
_ASSET_SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _spot(asset):
    sym = _ASSET_SYM[asset]
    try:
        d = json.loads(urllib.request.urlopen(
            f"https://api.binance.com/api/v3/ticker/price?symbol={sym}",
            timeout=15).read())
        return float(d["price"])
    except Exception:
        cb = "BTC-USD" if asset == "BTC" else "ETH-USD"
        d = json.loads(urllib.request.urlopen(
            f"https://api.coinbase.com/v2/prices/{cb}/spot", timeout=15).read())
        return float(d["data"]["amount"])


def _vol_5m(asset, n=288):
    """Std of recent 5m log returns (~1 day lookback). Coinbase fallback if
    Binance is geo-blocked."""
    sym = _ASSET_SYM[asset]
    try:
        d = json.loads(urllib.request.urlopen(
            f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=5m&limit={n}",
            timeout=20).read())
        closes = [float(r[4]) for r in d]
    except Exception:
        cb = "BTC-USD" if asset == "BTC" else "ETH-USD"
        d = json.loads(urllib.request.urlopen(
            f"https://api.exchange.coinbase.com/products/{cb}/candles?granularity=300",
            timeout=20).read())
        closes = [row[4] for row in sorted(d, key=lambda r: r[0])]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var)


def _epoch(iso):
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def model_prob(S, K, secs_to_close, sigma_5m):
    """P(S_close > K) under a driftless lognormal over the horizon."""
    tau_bars = max(secs_to_close / 300.0, 1e-6)
    sigma_tau = sigma_5m * math.sqrt(tau_bars)
    if sigma_tau <= 0:
        return 1.0 if S > K else 0.0
    return _norm_cdf(math.log(S / K) / sigma_tau)


def _open_tickers():
    rows = db._rows("SELECT symbol FROM executions "
                    f"WHERE venue='kalshi' AND outcome={db.PH}", ("",))
    return {r["symbol"] for r in rows}


def collect(probe=False):
    held = _open_tickers() if not probe else set()
    now = int(time.time())
    placed = 0
    for asset in ("BTC", "ETH"):
        try:
            S = _spot(asset); sigma = _vol_5m(asset)
        except Exception as e:
            print(f"  {asset}: price/vol unavailable: {str(e)[:80]}")
            continue
        for m in kx.crypto_markets(asset):
            if placed >= MAX_NEW_PER_RUN:
                break
            t = m["ticker"]
            if t in held or m["kind"] != "above" or not m["strike"]:
                continue
            close = _epoch(m["close_time"]); secs = close - now
            if secs <= 60 or secs > WINDOW_S:
                continue
            p = model_prob(S, m["strike"], secs, sigma)
            if not (P_LO < p < P_HI):              # skip degenerate calls
                continue
            side = "yes" if p >= 0.5 else "no"
            if probe:
                print(f"  {t} K={m['strike']} S={S:.0f} model_p={p:.3f} -> {side} "
                      f"(implied {m['implied']}) {secs // 60}m to close")
                placed += 1
                continue
            sid = db.record_signal(
                STRATEGY, t, "event", 1 if side == "yes" else -1, "vol_model",
                detail={"strike": m["strike"], "spot": round(S, 2),
                        "model_p": round(p, 4), "implied": m["implied"],
                        "sigma_5m": round(sigma, 6), "close_time": m["close_time"],
                        "secs_to_close": secs})
            db.record_execution(sid, "kalshi", t, side, round(p, 4), None, None,
                                ref=f"kalshi:{t}")
            placed += 1; held.add(t)
    return placed


def resolve_open():
    rows = db._rows("SELECT * FROM executions "
                    f"WHERE venue='kalshi' AND outcome={db.PH}", ("",))
    n = 0
    for e in rows:
        try:
            m = kx.market(e["symbol"])
        except Exception:
            continue
        status = (m.get("status") or "").lower()
        result = (m.get("result") or "").lower()
        if status not in ("settled", "finalized") or result not in ("yes", "no"):
            continue
        won = int(result == e["side"])
        db.resolve_execution(e["id"], 1.0 if result == "yes" else 0.0, result,
                             won, 100.0 if won else -100.0, 1)
        n += 1
    return n


if __name__ == "__main__":
    import sys
    if "--probe" in sys.argv:
        collect(probe=True)
    else:
        db.init()
        r = resolve_open(); p = collect()
        print(f"kalshi_crypto_model: resolved {r}, recorded {p} new predictions")
