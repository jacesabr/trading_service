"""
venues.py — Real paper-trading venue adapters (run alongside the internal
Binance-candle simulation so we can compare sim vs real execution).

Each adapter is GATED on its env credentials: if the keys aren't set, the venue
is simply absent and the runner falls back to sim only — so this is always safe
to deploy. Adapters never raise into the runner; any error -> the trade is
skipped for that venue.

Phase 1 (here): an open_trade returns a REAL entry fill priced off the venue's
own live book (crossing the spread), capturing each venue's actual spread/price
basis. Outcome is then resolved by the runner from Binance candles using that
real entry. Phase 2 (added per venue during live verification with keys): place
native bracket orders and resolve from the venue's own fills/SL-TP.

Credentials (set on the Render worker):
  OANDA   : OANDA_TOKEN, OANDA_ACCOUNT, OANDA_ENV=practice
  Alpaca  : ALPACA_KEY, ALPACA_SECRET            (paper account)
  Binance : BINANCE_TESTNET_KEY, BINANCE_TESTNET_SECRET   (Phase 2)
  Kalshi  : KALSHI_KEY, KALSHI_SECRET                     (Phase 2)
"""
import json
import os
import urllib.request


def _http(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers=headers or {})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


class Venue:
    name = "base"

    def available(self):
        return False

    def symbol_for(self, binance_symbol):
        return None

    def open_trade(self, binance_symbol, direction, stop, target, kind):
        """Return dict(fill=<price>, ref=<str>) priced on the venue's real book,
        or None if unsupported/unavailable/error. Never raises."""
        return None


class OandaVenue(Venue):
    """OANDA v20 practice — FX/metals + BTC_USD/ETH_USD CFDs. Wrapper in
    forex_oanda.py. Phase 1: real practice bid/ask -> fill crosses the spread."""
    name = "oanda"
    MAP = {"BTCUSDT": "BTC_USD", "ETHUSDT": "ETH_USD"}

    def available(self):
        return bool(os.environ.get("OANDA_TOKEN") and os.environ.get("OANDA_ACCOUNT"))

    def symbol_for(self, s):
        return self.MAP.get(s)

    def open_trade(self, s, direction, stop, target, kind):
        inst = self.symbol_for(s)
        if not inst:
            return None
        try:
            import forex_oanda
            bid, ask = forex_oanda.price(inst)
            return dict(fill=float(ask if direction > 0 else bid), ref=f"oanda:{inst}")
        except Exception:
            return None


class AlpacaVenue(Venue):
    """Alpaca paper — crypto (and US stocks). Phase 1: latest crypto quote ->
    fill crosses the spread. Phase 2: real bracket orders via /v2/orders."""
    name = "alpaca"
    MAP = {"BTCUSDT": "BTC/USD", "ETHUSDT": "ETH/USD",
           "SOLUSDT": "SOL/USD", "DOGEUSDT": "DOGE/USD", "XRPUSDT": "XRP/USD"}

    def available(self):
        return bool(os.environ.get("ALPACA_KEY") and os.environ.get("ALPACA_SECRET"))

    def symbol_for(self, s):
        return self.MAP.get(s)

    def _hdr(self):
        return {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"],
                "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}

    def open_trade(self, s, direction, stop, target, kind):
        sym = self.symbol_for(s)
        if not sym:
            return None
        try:
            enc = sym.replace("/", "%2F")
            q = _http("https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes"
                      f"?symbols={enc}", headers=self._hdr())
            quote = q["quotes"][sym]
            bid = float(quote["bp"]); ask = float(quote["ap"])
            if bid <= 0 or ask <= 0:
                return None
            return dict(fill=float(ask if direction > 0 else bid), ref=f"alpaca:{sym}")
        except Exception:
            return None


class BinanceTestnetVenue(Venue):
    """Binance spot testnet — Phase 2 (real signed testnet orders). Present so
    keys activate it; open_trade is a no-op until the Phase-2 build."""
    name = "binance_testnet"
    MAP = {k: k for k in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")}

    def available(self):
        return bool(os.environ.get("BINANCE_TESTNET_KEY")
                    and os.environ.get("BINANCE_TESTNET_SECRET"))

    def symbol_for(self, s):
        return self.MAP.get(s)

    def open_trade(self, s, direction, stop, target, kind):
        return None   # Phase 2: signed POST /api/v3/order on testnet.binance.vision


class KalshiVenue(Venue):
    """Kalshi — regulated binary event exchange (closest Polymarket analog with
    an API). Phase 2 (binary strategies only)."""
    name = "kalshi"

    def available(self):
        return bool(os.environ.get("KALSHI_KEY") and os.environ.get("KALSHI_SECRET"))

    def symbol_for(self, s):
        return None   # Phase 2: map to BTC/ETH price-range markets

    def open_trade(self, s, direction, stop, target, kind):
        return None


_ALL = [OandaVenue(), AlpacaVenue(), BinanceTestnetVenue(), KalshiVenue()]


def active_venues():
    """Venues whose credentials are present (and that can actually trade)."""
    return [v for v in _ALL if v.available()]


def status():
    """For diagnostics: which venues are configured."""
    return {v.name: v.available() for v in _ALL}
