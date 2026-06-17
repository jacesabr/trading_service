"""daily.py — the scheduled lab heartbeat (deterministic; runs every ~8h).

What runs every cycle, against REAL APIs, paper-only:
  1. Resolve matured Kalshi crypto predictions from Kalshi's finalized results.
  2. Collect new model predictions on near-money Kalshi markets (live spot+vol).
  3. Print a heartbeat the scheduler/log captures.

This is the dependable, no-LLM core of the daily run. The Claude Code research
agent (AGENT.md) layers research/validation/upkeep on top via the lab CLI; this
script is what guarantees positions get recorded and settled on schedule even if
the agent does nothing.

Run: python daily.py   (uses DATABASE_URL if set, else local tracker.db)
"""
import os
import time

import db

# Local SIMULATION collection (Kalshi paper bets + equity-bar paper trades) is OFF
# by default — we track REAL broker fills only (Alpaca/Bybit). Set LAB_SIM=1 to
# re-enable the self-resolved sim data collection.
SIM = os.environ.get("LAB_SIM") == "1"


def main():
    db.init()
    started = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
    out = {"started": started}

    if SIM:
        # --- Kalshi crypto model (real API, paper SIM) ---
        try:
            import kalshi_paper as kp
            out["kalshi"] = {"resolved": kp.resolve_open(), "recorded": kp.collect()}
        except Exception as e:
            out["kalshi"] = {"error": str(e)[:200]}

        # --- Equities on Alpaca (real bars, paper SIM) ---
        try:
            import equity_paper as eq
            eq.ensure_manifests()
            out["equity"] = {"resolved": eq.resolve_open(), "recorded": eq.collect()}
        except Exception as e:
            out["equity"] = {"error": str(e)[:200]}

    # --- REAL Alpaca equity bracket orders (broker-managed OCO) — always on ---
    try:
        import equity_orders
        import equity_paper as eq
        eq.ensure_manifests()
        ores = equity_orders.resolve_open()
        oplaced = eq.place_live_orders()
        out["equity_orders"] = {"resolved": ores, "placed": oplaced}
    except Exception as e:
        out["equity_orders"] = {"error": str(e)[:200]}

    # --- TradingView Ideas: resolve open demo brackets (real klines, no LLM) ---
    # Resolution is pure price data, so it runs unattended here every cycle; the
    # scrape + chart-read (which need Claude Code) stay in the manual runbook.
    try:
        from ideas import execute as ideas_exec
        ideas_exec._ensure_cols()
        nw, _, _ = ideas_exec.work_orders()      # extracted -> pending (resting orders)
        rr = ideas_exec.resolve_open()           # fill pending + resolve open
        out["ideas"] = {"pending": nw, **rr}
    except Exception as ex:
        out["ideas"] = {"error": str(ex)[:200]}

    # --- summary line ---
    k = out.get("kalshi", {}); e = out.get("equity", {}); o = out.get("equity_orders", {})
    iv = out.get("ideas", {})
    print(f"[daily {started}] kalshi: resolved={k.get('resolved','?')} "
          f"recorded={k.get('recorded','?')}"
          + (f" ERR={k['error']}" if "error" in k else "")
          + f" | equity: resolved={e.get('resolved','?')} "
          f"recorded={e.get('recorded','?')}"
          + (f" ERR={e['error']}" if "error" in e else "")
          + f" | eq_orders: resolved={o.get('resolved','?')} "
          f"placed={o.get('placed','?')}"
          + (f" ERR={o['error']}" if "error" in o else "")
          + f" | ideas: pending={iv.get('pending','?')} "
          f"filled={iv.get('filled','?')} resolved={iv.get('resolved','?')} "
          f"expired={iv.get('expired','?')}"
          + (f" ERR={iv['error']}" if "error" in iv else ""))
    return out


if __name__ == "__main__":
    main()
