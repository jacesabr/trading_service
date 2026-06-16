"""adapters.deribit_client — Deribit v2 JSON-RPC-over-REST client (testnet).

Auth: client_credentials (client_id + client_secret) -> bearer access_token,
cached and auto-refreshed just before expiry. Public methods need no token;
private methods (account/trade) attach the bearer.

Money floor: defaults to TESTNET (DERIBIT_API_BASE = test.deribit.com). The lab
only points this at testnet — real-money Deribit would require the same explicit
LIVE_BUDGET_ARMED gate as every other venue.

Credentials (gitignored .env / cloud env):
  DERIBIT_CLIENT_ID, DERIBIT_CLIENT_SECRET, DERIBIT_API_BASE
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://test.deribit.com/api/v2"


def _load_env():
    if os.environ.get("DERIBIT_CLIENT_ID"):
        return
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


class Deribit:
    def __init__(self):
        _load_env()
        self.base = os.environ.get("DERIBIT_API_BASE", DEFAULT_BASE).rstrip("/")
        self.cid = os.environ.get("DERIBIT_CLIENT_ID")
        self.sec = os.environ.get("DERIBIT_CLIENT_SECRET")
        self._tok = None
        self._exp = 0.0

    @property
    def is_testnet(self):
        return "test.deribit.com" in self.base

    def _raw(self, path, params=None, headers=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers or {})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            try:
                err = json.loads(body).get("error", {})
                raise RuntimeError(f"Deribit {path} {e.code}: {err}")
            except (ValueError, AttributeError):
                raise RuntimeError(f"Deribit {path} {e.code}: {body}")
        if "error" in r:
            raise RuntimeError(f"Deribit {path} error: {r['error']}")
        return r.get("result")

    def _token(self):
        if self._tok and time.time() < self._exp - 30:
            return self._tok
        r = self._raw("/public/auth",
                      {"grant_type": "client_credentials",
                       "client_id": self.cid, "client_secret": self.sec})
        self._tok = r["access_token"]
        self._exp = time.time() + float(r.get("expires_in", 900))
        return self._tok

    def public(self, method, **params):
        return self._raw(f"/public/{method}", params)

    def private(self, method, **params):
        return self._raw(f"/private/{method}", params,
                         headers={"Authorization": f"Bearer {self._token()}"})


if __name__ == "__main__":
    d = Deribit()
    print("base:", d.base, "| testnet:", d.is_testnet,
          "| client:", (d.cid or "")[:8])
    print("auth:", "ok" if d._token() else "fail")
    try:
        s = d.private("get_account_summary", currency="BTC")
        print("account BTC equity:", s.get("equity"))
    except RuntimeError as e:
        print("private call (needs account:read / trade scopes):", e)
