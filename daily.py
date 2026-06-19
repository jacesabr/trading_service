"""daily.py — the scheduled algo-trading heartbeat (deterministic, every 2h).

Places + resolves the REAL broker bracket batteries, paper-only:
  - Bybit demo crypto perps (crypto_paper, 24/7)
  - Alpaca paper equities (equity_paper, market hours)
Broker fills only — no self-resolved sim. Prints a heartbeat the cron log captures.

Run: python daily.py   (uses DATABASE_URL if set, else local tracker.db)
"""
import time

import db


def main():
    db.init()
    started = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
    out = {"started": started}

    # --- REAL Alpaca equity bracket orders (broker-managed OCO) ---
    try:
        import equity_orders
        import equity_paper as eq
        eq.ensure_manifests()
        out["equity_orders"] = {"resolved": equity_orders.resolve_open(),
                                "placed": eq.place_live_orders()}
    except Exception as e:
        out["equity_orders"] = {"error": str(e)[:200]}

    # --- REAL Bybit demo crypto bracket battery (24/7, broker-held TP/SL) ---
    try:
        import crypto_paper as cx
        cx.ensure_manifests()
        out["crypto_orders"] = {"resolved": cx.resolve_open(),
                                "placed": cx.place_live_orders()}
    except Exception as e:
        out["crypto_orders"] = {"error": str(e)[:200]}

    o = out.get("equity_orders", {}); co = out.get("crypto_orders", {})
    print(f"[daily {started}] eq_orders: resolved={o.get('resolved','?')} "
          f"placed={o.get('placed','?')}"
          + (f" ERR={o['error']}" if "error" in o else "")
          + f" | crypto_orders: resolved={co.get('resolved','?')} "
          f"placed={co.get('placed','?')}"
          + (f" ERR={co['error']}" if "error" in co else ""))
    return out


if __name__ == "__main__":
    main()
