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
import time

import db


def main():
    db.init()
    started = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
    out = {"started": started}

    # --- Kalshi crypto model (real API, paper) ---
    try:
        import kalshi_paper as kp
        resolved = kp.resolve_open()
        recorded = kp.collect()
        out["kalshi"] = {"resolved": resolved, "recorded": recorded}
    except Exception as e:
        out["kalshi"] = {"error": str(e)[:200]}

    # --- Equities on Alpaca (real bars, paper) ---
    try:
        import equity_paper as eq
        eq.ensure_manifests()
        eres = eq.resolve_open()
        erec = eq.collect()
        out["equity"] = {"resolved": eres, "recorded": erec}
    except Exception as e:
        out["equity"] = {"error": str(e)[:200]}

    # --- summary line ---
    k = out.get("kalshi", {}); e = out.get("equity", {})
    print(f"[daily {started}] kalshi: resolved={k.get('resolved','?')} "
          f"recorded={k.get('recorded','?')}"
          + (f" ERR={k['error']}" if "error" in k else "")
          + f" | equity: resolved={e.get('resolved','?')} "
          f"recorded={e.get('recorded','?')}"
          + (f" ERR={e['error']}" if "error" in e else ""))
    return out


if __name__ == "__main__":
    main()
