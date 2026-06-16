"""adapters.binance_testnet — Binance SPOT testnet signed client (paper).

Real fills on the Binance spot matching engine (testnet.binance.vision) — the SAME
book the lab's crypto signals are computed from, so tight spreads and honest fills.
SPOT is long-only (no shorting; the futures testnet would add that). Requests are
signed HMAC-SHA256 over the query string, timestamped off Binance server time.

IMPORTANT — region: Binance blocks AUTHENTICATED / trading actions from some
regions (e.g. the US) even on testnet, while still serving public data. So signed
calls must run from an allowed region — the Frankfurt worker, which is already
where the runner lives because Binance 451-blocks US IPs. From a US dev box these
calls return -2015; that is expected, not a bad key.

Keys: BINANCE_TESTNET_KEY / BINANCE_TESTNET_SECRET (gitignored .env / worker env).
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("BINANCE_TESTNET_BASE", "https://testnet.binance.vision")


def _load_env():
    if os.environ.get("BINANCE_TESTNET_KEY"):
        return
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _keys():
    _load_env()
    return (os.environ.get("BINANCE_TESTNET_KEY"),
            os.environ.get("BINANCE_TESTNET_SECRET"))


def available():
    k, s = _keys()
    return bool(k and s)


def _get(url, headers=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_body": e.read().decode()[:200]}


def server_time():
    r = _get(f"{BASE}/api/v3/time")
    return r.get("serverTime") if isinstance(r, dict) else None


def book_ticker(symbol):
    """Public best bid/ask — works from any region."""
    return _get(f"{BASE}/api/v3/ticker/bookTicker?symbol={symbol}")


def _signed(path, params=None, method="GET"):
    key, sec = _keys()
    if not (key and sec):
        return {"_err": -1, "_body": "no BINANCE_TESTNET_KEY/SECRET"}
    p = dict(params or {})
    p["timestamp"] = server_time() or int(time.time() * 1000)
    p["recvWindow"] = 5000
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(sec.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{qs}&signature={sig}"
    return _get(url, headers={"X-MBX-APIKEY": key}, method=method)


def account():
    return _signed("/api/v3/account")


def market_buy(symbol, quote_usdt):
    """Spend `quote_usdt` USDT to market-buy `symbol` (quoteOrderQty)."""
    return _signed("/api/v3/order", {"symbol": symbol, "side": "BUY",
                                     "type": "MARKET",
                                     "quoteOrderQty": quote_usdt}, "POST")


def market_sell(symbol, quantity):
    """Market-sell `quantity` of the base asset."""
    return _signed("/api/v3/order", {"symbol": symbol, "side": "SELL",
                                     "type": "MARKET", "quantity": quantity},
                   "POST")


def probe():
    """One signed account call — confirms whether signed/trading auth works from
    this host's region. Returns a compact status dict for logging."""
    if not available():
        return {"ok": False, "why": "no keys"}
    a = account()
    if isinstance(a, dict) and a.get("balances") is not None:
        nz = [(b["asset"], b["free"]) for b in a["balances"]
              if float(b["free"]) > 0][:8]
        return {"ok": True, "canTrade": a.get("canTrade"), "balances": nz}
    return {"ok": False, "error": a}


if __name__ == "__main__":
    print("base:", BASE, "| keys present:", available())
    print("public bookTicker BTCUSDT:", book_ticker("BTCUSDT"))
    print("signed probe:", probe())
