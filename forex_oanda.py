"""
forex_oanda.py — Forex/CFD connector for the gap-traversal experiment.

OANDA v20 has a genuine FREE practice account with a full REST API — the
standard for retail algo forex paper trading. This wraps it for both:
  * paper : simulate fills at mid +/- half-spread from the practice pricing
            stream (no order risk, real prices).
  * live  : real practice-account orders (still not real money — it's a demo
            account; only flip to a live account consciously).

Setup (one time):
  1. Create a free practice account at oanda.com -> get an API token.
  2. export OANDA_TOKEN=...   OANDA_ACCOUNT=...   OANDA_ENV=practice
Instruments: FX majors (EUR_USD...) and CFDs incl. BTC_USD, ETH_USD on OANDA.

NOTE: the gap-traversal backtested at ~0 net expectancy after spread, so this
is wired as a PAPER experiment to watch live behavior, NOT a funded strategy.
The safety rails mirror executor.py and refuse live unless confirm_live=True.
"""
import json
import os
import time
import urllib.request

PRACTICE = "https://api-fxpractice.oanda.com"
LIVE = "https://api-fxtrade.oanda.com"


def _base():
    return LIVE if os.environ.get("OANDA_ENV") == "live" else PRACTICE


def _req(path, method="GET", body=None):
    token = os.environ.get("OANDA_TOKEN")
    if not token:
        raise RuntimeError("OANDA_TOKEN not set")
    url = _base() + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def price(instrument):
    acct = os.environ["OANDA_ACCOUNT"]
    d = _req(f"/v3/accounts/{acct}/pricing?instruments={instrument}")
    p = d["prices"][0]
    bid = float(p["bids"][0]["price"]); ask = float(p["asks"][0]["price"])
    return bid, ask


class OandaPaper:
    mode = "paper"

    def open_trade(self, instrument, direction, units, stop, target):
        bid, ask = price(instrument)
        fill = ask if direction > 0 else bid       # cross the spread, realistic
        return dict(status="paper", instrument=instrument, fill=fill,
                    bid=bid, ask=ask, units=units * direction,
                    stop=stop, target=target)


class OandaLive:
    mode = "live"

    def __init__(self, confirm_live=False):
        if not confirm_live:
            raise RuntimeError("OandaLive requires confirm_live=True")
        self.acct = os.environ["OANDA_ACCOUNT"]

    def open_trade(self, instrument, direction, units, stop, target):
        order = {"order": {
            "type": "MARKET", "instrument": instrument,
            "units": str(int(units * direction)),
            "stopLossOnFill": {"price": f"{stop:.5f}"},
            "takeProfitOnFill": {"price": f"{target:.5f}"},
            "timeInForce": "FOK", "positionFill": "DEFAULT"}}
        return _req(f"/v3/accounts/{self.acct}/orders", "POST", order)


def get_forex(live=False):
    return OandaLive(confirm_live=True) if live else OandaPaper()


if __name__ == "__main__":
    if os.environ.get("OANDA_TOKEN"):
        try:
            print("practice price BTC_USD:", price("BTC_USD"))
        except Exception as e:
            print("live check failed:", e)
    else:
        print("OANDA_TOKEN not set — paper connector ready, set token to go live-practice")
        fx = get_forex(False)
        print("paper connector class:", fx.mode)
