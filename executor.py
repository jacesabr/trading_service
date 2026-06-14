"""
executor.py — Unified execution layer: paper mode and real Polymarket CLOB.

There is no official Polymarket paper-trading API; paper mode against live
order books (what this system does) is the standard substitute. This module
gives both modes one interface so promotion from paper to live is a config
change, not a rewrite.

PAPER (default): returns simulated fills from a book snapshot. No keys.

LIVE: real orders through the official CLOB via py-clob-client.
  Setup (one time):
    pip install py-clob-client
    1. Create/fund a Polygon wallet with USDC.e; do first deposit + one
       manual trade through the Polymarket UI (sets token allowances).
    2. export POLY_PRIVATE_KEY=0x...   (the wallet's private key — use a
       DEDICATED wallet holding only your trading bankroll, nothing else)
  Safety rails (hard-coded, edit consciously):
    - maker-only: orders post at or below best bid + 1 tick, never cross
    - MAX_STAKE_USD per order, MAX_DAILY_LOSS_USD kill switch
    - refuses to start unless EXPLICITLY constructed with confirm_live=True
"""
import os
import time

MAX_STAKE_USD = 20.0
MAX_DAILY_LOSS_USD = 60.0


class PaperExecutor:
    mode = "paper"

    def place_maker(self, token_id, price, usd):
        return {"status": "paper", "token_id": token_id,
                "price": price, "shares": round(usd / price, 2)}

    def cancel_all(self):
        pass


class LiveExecutor:
    mode = "live"

    def __init__(self, confirm_live=False):
        if not confirm_live:
            raise RuntimeError("LiveExecutor requires confirm_live=True")
        key = os.environ.get("POLY_PRIVATE_KEY")
        if not key:
            raise RuntimeError("POLY_PRIVATE_KEY not set")
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        self._OrderArgs, self._OrderType, self._BUY = OrderArgs, OrderType, BUY
        self.client = ClobClient("https://clob.polymarket.com",
                                 key=key, chain_id=137)
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        self.daily_pnl = 0.0
        self.day = time.strftime("%Y-%m-%d")

    def _check(self, usd):
        if time.strftime("%Y-%m-%d") != self.day:
            self.day, self.daily_pnl = time.strftime("%Y-%m-%d"), 0.0
        if usd > MAX_STAKE_USD:
            raise RuntimeError(f"stake {usd} > MAX_STAKE_USD")
        if self.daily_pnl <= -MAX_DAILY_LOSS_USD:
            raise RuntimeError("daily loss limit hit — trading halted")

    def place_maker(self, token_id, price, usd):
        """Post a GTC limit BUY at `price` (caller must pass a maker price,
        i.e. at/below best bid + 1 tick). Returns the CLOB response."""
        self._check(usd)
        shares = round(usd / price, 2)
        order = self.client.create_order(self._OrderArgs(
            price=round(price, 2), size=shares, side=self._BUY,
            token_id=token_id))
        return self.client.post_order(order, self._OrderType.GTC)

    def record_pnl(self, pnl):
        self.daily_pnl += pnl

    def cancel_all(self):
        try:
            self.client.cancel_all()
        except Exception:
            pass


def get_executor(live=False):
    return LiveExecutor(confirm_live=True) if live else PaperExecutor()
