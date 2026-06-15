"""adapters.kalshi_client — authenticated Kalshi REST client (RSA API keys).

Kalshi signs each request with RSA-PSS(SHA256) over `timestamp + METHOD + path`,
sending three headers: KALSHI-ACCESS-KEY (key id), KALSHI-ACCESS-TIMESTAMP (ms),
KALSHI-ACCESS-SIGNATURE (base64). Public market data also works unsigned, but we
sign everything so the same client serves portfolio/order calls later.

Credentials (gitignored .env / cloud env):
  KALSHI_API_KEY_ID         the key UUID
  KALSHI_PRIVATE_KEY        PEM contents  (cloud)  -- OR --
  KALSHI_PRIVATE_KEY_PATH   path to the PEM file   (local)
  KALSHI_API_BASE           defaults to the prod elections host
"""
import base64
import json
import os
import time
import urllib.request
import urllib.error

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _load_env():
    """Minimal .env loader (no dependency on python-dotenv)."""
    if os.environ.get("KALSHI_API_KEY_ID"):
        return
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _private_key():
    pem = os.environ.get("KALSHI_PRIVATE_KEY")
    if not pem:
        p = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        if p:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            p = p if os.path.isabs(p) else os.path.join(base, p)
            pem = open(p).read()
    if not pem:
        raise RuntimeError("no KALSHI_PRIVATE_KEY / KALSHI_PRIVATE_KEY_PATH set")
    return serialization.load_pem_private_key(pem.encode(), password=None)


class Kalshi:
    def __init__(self):
        _load_env()
        self.base = os.environ.get("KALSHI_API_BASE", DEFAULT_BASE).rstrip("/")
        self.key_id = os.environ.get("KALSHI_API_KEY_ID")
        self._pk = None

    def _sign(self, ts, method, path):
        if self._pk is None:
            self._pk = _private_key()
        msg = f"{ts}{method}{path}".encode()
        sig = self._pk.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
        return base64.b64encode(sig).decode()

    def request(self, method, path, params=None, body=None):
        """path is relative to the host, e.g. '/trade-api/v2/markets'. The
        signature covers the path WITHOUT query string."""
        # full path used for signing = the v2 path portion of the URL
        from urllib.parse import urlparse, urlencode
        full = self.base + path
        sign_path = urlparse(self.base).path + path
        if params:
            full += "?" + urlencode(params)
        ts = str(int(time.time() * 1000))
        headers = {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, sign_path),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(full, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Kalshi {method} {path} -> {e.code}: "
                               f"{e.read().decode()[:300]}")

    # ---- convenience reads ----
    def exchange_status(self):
        return self.request("GET", "/exchange/status")

    def markets(self, **params):
        return self.request("GET", "/markets", params=params or None)

    def market(self, ticker):
        return self.request("GET", f"/markets/{ticker}")

    def orderbook(self, ticker, depth=10):
        return self.request("GET", f"/markets/{ticker}/orderbook",
                            params={"depth": depth})

    def candlesticks(self, series_ticker, ticker, start_ts, end_ts,
                     period_interval=60):
        return self.request(
            "GET",
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            params={"start_ts": start_ts, "end_ts": end_ts,
                    "period_interval": period_interval})


if __name__ == "__main__":
    k = Kalshi()
    print("base:", k.base, "| key:", (k.key_id or "")[:8] + "…")
    print("exchange status:", k.exchange_status())
    ms = k.markets(limit=3, status="open")
    mk = ms.get("markets", [])
    print(f"open markets sample: {len(mk)}")
    for m in mk[:3]:
        print(f"  {m.get('ticker')}: {m.get('title','')[:60]} "
              f"yes_bid={m.get('yes_bid')} yes_ask={m.get('yes_ask')}")
