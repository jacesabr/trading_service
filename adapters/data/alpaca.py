"""adapters.data.alpaca — Alpaca equity market data + paper account access.

Free IEX feed for bars (backtest + live), and the paper trading account for
real demo fills. Keys: ALPACA_KEY / ALPACA_SECRET (env or .env).

  bars(symbol, tf, limit)   DataFrame[ts,open,high,low,close,volume] (ts ms)
  account()                 paper account snapshot
  clock()                   market open/closed + next open/close
"""
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

import pandas as pd

DATA = "https://data.alpaca.markets/v2"
PAPER = "https://paper-api.alpaca.markets/v2"
TF = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "1d": "1Day"}
# how far back to pull so indicators (need ~200 bars) have enough history
LOOKBACK_DAYS = {"1m": 8, "5m": 30, "15m": 60, "1h": 200, "1d": 800}


def _load_env():
    if os.environ.get("ALPACA_KEY"):
        return
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _hdr():
    _load_env()
    return {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}


def _get(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers=_hdr()), timeout=30).read())


def bars(symbol, tf, limit=300, feed="iex"):
    """Recent bars for symbol at tf. Returns DataFrame oldest->newest. Pulls a
    historical `start` window (IEX returns only the recent session otherwise)."""
    days = LOOKBACK_DAYS.get(tf, 30)
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    rows, page = [], None
    for _ in range(6):                           # paginate up to limit
        params = {"timeframe": TF.get(tf, tf), "start": start,
                  "limit": min(limit, 10000), "feed": feed, "adjustment": "raw"}
        if page:
            params["page_token"] = page
        d = _get(f"{DATA}/stocks/{symbol}/bars?{urllib.parse.urlencode(params)}")
        rows += d.get("bars", []) or []
        page = d.get("next_page_token")
        if not page or len(rows) >= limit:
            break
    rows = rows[-limit:] if len(rows) > limit else rows
    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    out = pd.DataFrame([{
        "ts": int(datetime.fromisoformat(r["t"].replace("Z", "+00:00"))
                  .timestamp() * 1000),
        "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"],
        "volume": r["v"]} for r in rows])
    return out.sort_values("ts").reset_index(drop=True)


def account():
    return _get(f"{PAPER}/account")


def clock():
    return _get(f"{PAPER}/clock")


if __name__ == "__main__":
    print("clock:", clock())
    df = bars("AAPL", "5m", 10)
    print(f"AAPL 5m bars: {len(df)}")
    print(df.tail(3).to_string(index=False))
