"""adapters.data.kalshi — Kalshi market data for the lab.

Surfaces the crypto price-threshold markets (KXBTCD/KXETHD daily, KXBTC/KXETH
ranges) that settle on BTC/ETH prices — a leak-free research surface: model the
settlement probability from Binance and compare to the market-implied price.

  crypto_markets(asset)   open markets for an asset, with implied prob + book
  market(ticker)          a single market (for resolution: status + result)
  candles(...)            historical candlesticks (for backtest)

Thresholds are encoded in the ticker suffix: -T<price> = "above price",
-B<price> = a between/range bucket. We parse the strike for the model.
"""
import re

from adapters.kalshi_client import Kalshi

_K = None

SERIES = {
    "BTC": ["KXBTCD", "KXBTC"],
    "ETH": ["KXETHD", "KXETH"],
}


def client():
    global _K
    if _K is None:
        _K = Kalshi()
    return _K


def _implied(m):
    """Market-implied P(yes) from the book mid, in [0,1]. Kalshi quotes cents."""
    bid, ask = m.get("yes_bid"), m.get("yes_ask")
    if bid is None and ask is None:
        return None
    if bid is None:
        bid = ask
    if ask is None:
        ask = bid
    return round((bid + ask) / 200.0, 4)            # cents -> prob


def _strike(ticker):
    mt = re.search(r"-[TB](\d+(?:\.\d+)?)$", ticker)
    return float(mt.group(1)) if mt else None


def crypto_markets(asset="BTC", status="open", limit=200):
    """Open crypto markets for asset with parsed strike + implied prob + book."""
    k = client(); out = []
    for st in SERIES.get(asset, []):
        try:
            r = k.markets(series_ticker=st, status=status, limit=limit)
        except Exception:
            continue
        for m in r.get("markets", []):
            out.append({
                "ticker": m.get("ticker"),
                "series": st,
                "title": m.get("title", ""),
                "kind": "above" if "-T" in m.get("ticker", "") else "range",
                "strike": _strike(m.get("ticker", "")),
                "yes_bid": m.get("yes_bid"), "yes_ask": m.get("yes_ask"),
                "implied": _implied(m),
                "close_time": m.get("close_time"),
                "status": m.get("status"),
                "volume": m.get("volume", 0),
            })
    return out


def market(ticker):
    return client().market(ticker).get("market", {})


def candles(series_ticker, ticker, start_ts, end_ts, period_interval=60):
    return client().candlesticks(series_ticker, ticker, start_ts, end_ts,
                                 period_interval).get("candlesticks", [])
