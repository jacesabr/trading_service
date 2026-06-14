"""
analyst.py — Daily LLM analysis run over the paper-trading log.

What it does (run daily via cron/pm2):
  1. Computes hard numbers from paper_trades.csv (the LLM never sees raw
     rows — it sees verified statistics, so it can't hallucinate the data).
  2. Sends them to the configured provider (default: Claude / Fable) with a
     tightly-scoped brief: health check, calibration verdict, drift per
     rule, anomalies, and 1-3 concrete TESTABLE experiment suggestions.
  3. Saves reports/YYYY-MM-DD.md (dashboard shows the latest) and appends
     suggested experiments to SUGGESTIONS.md.

Safety rail: suggestions are a backlog for YOU to approve. This script
never edits rules.json. Auto-applying LLM-suggested rules to a trading
system is how you end up trading hallucinations.

Usage:
  python3 analyst.py            # full run (needs API key)
  python3 analyst.py --dry      # print the prompt, call nothing
  python3 analyst.py --provider nvidia
"""
import json
import os
import sys
import time
import numpy as np
import pandas as pd

LOG = "paper_trades.csv"
SIZE_USD = 100.0
BACKTEST = {"overbought_strong": 0.56, "overbought_rsi14_bbz": 0.56,
            "oversold_bounce": 0.53}

SYSTEM = (
 "You are the daily reviewing analyst for a small quantitative experiment "
 "trading Polymarket 5-minute BTC/ETH Up-or-Down markets from mean-reversion "
 "signals. You receive verified statistics only. Be blunt and numeric. "
 "Multiple-comparison discipline applies: do not call an edge real below "
 "|z|=2, and treat sub-100-sample slices as anecdotes. Structure: "
 "1) System health  2) Calibration verdict (win rate vs entry price is the "
 "only number that matters)  3) Per-rule drift vs backtest  4) Anomalies  "
 "5) At most 3 suggested experiments, each with a falsifiable success "
 "criterion and the data needed. Never suggest increasing size while EV "
 "after fees is not significantly positive.")


def gather():
    df = pd.read_csv(LOG)
    res = df[df["outcome"].notna() & (df["outcome"] != "")].copy()
    res["won"] = res["won"].astype(int)
    now = time.time()
    day = res[res["ts"] > now - 86400]
    s = {"generated_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
         "total_signals": int(len(df)), "resolved": int(len(res)),
         "last_signal_age_min": round((now - df["ts"].max()) / 60, 1),
         "last_24h": {"n": int(len(day))}}
    for name, g in (("overall", res), ("last_24h_detail", day)):
        if len(g) == 0:
            continue
        z = (g["won"].mean() - 0.5) / np.sqrt(0.25 / len(g))
        s[name] = {"n": int(len(g)), "hit_rate": round(g["won"].mean(), 4),
                   "z_vs_coin": round(float(z), 2),
                   "avg_entry_price": round(g["taker_fill"].mean(), 3),
                   "ev_per_bet_taker": round(g["pnl_taker"].mean() / SIZE_USD, 4),
                   "ev_per_bet_maker": round(g["pnl_maker_if_filled"].mean() / SIZE_USD, 4)}
    s["per_rule"] = []
    for rule, g in res.groupby("rule"):
        s["per_rule"].append({"rule": rule, "n": int(len(g)),
                              "live_hit": round(g["won"].mean(), 4),
                              "backtest_hit": BACKTEST.get(rule),
                              "ev_taker": round(g["pnl_taker"].mean() / SIZE_USD, 4)})
    res["bucket"] = (res["taker_fill"] * 50).round() / 50
    s["calibration"] = [
        {"entry_price": round(float(b), 2), "n": int(len(g)),
         "win_rate": round(g["won"].mean(), 3)}
        for b, g in res.groupby("bucket") if len(g) >= 10]
    return s


def main():
    dry = "--dry" in sys.argv
    provider = sys.argv[sys.argv.index("--provider") + 1] \
        if "--provider" in sys.argv else "anthropic"
    stats = gather()
    prompt = ("Daily review. Verified statistics from the live paper log:\n\n"
              + json.dumps(stats, indent=1)
              + "\n\nCurrent rules:\n" + open("rules.json").read()
              + "\nWrite the report.")
    if dry:
        print(SYSTEM, "\n\n---\n\n", prompt[:3000]); return
    from llm_client import ask
    report = ask(prompt, provider=provider, system=SYSTEM)
    os.makedirs("reports", exist_ok=True)
    path = f"reports/{time.strftime('%Y-%m-%d')}.md"
    with open(path, "w") as f:
        f.write(report)
    sug = [ln for ln in report.splitlines() if ln.strip().lower()
           .startswith(("experiment", "- experiment", "* experiment"))]
    if sug:
        with open("SUGGESTIONS.md", "a") as f:
            f.write(f"\n## {time.strftime('%Y-%m-%d')} ({provider})\n"
                    + "\n".join(sug) + "\n")
    print(f"report saved: {path} ({len(report)} chars)")


if __name__ == "__main__":
    main()
