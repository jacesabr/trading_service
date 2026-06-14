"""
trade_backtest.py — Trade-level test of the Zone Breaks rules.

Trade definition (exactly the script's own state machine):
  LONG : bar closes above zone k's top (k=1..7)  -> enter at NEXT bar's open
         target = zone k+1's bottom (limit fill if any bar's high reaches it)
         stop   = a CLOSE back below zone k's bottom -> exit at that close
  SHORT: mirror (breakdown of zone k, k=2..8; target = zone k-1's top;
         stop = a close back above zone k's top)

Honesty rules:
  * Entry is next bar's OPEN (you can't act on a close you just observed).
  * If the engine resolved the setup on the break bar itself, the trade is
    untradeable and skipped (counted separately).
  * When a bar satisfies both target and stop, the engine (like Pine) checks
    the stop first — conservative.
  * Unresolved trades at end of data are marked to the final close.
  * Fees: configurable bps per side (default 5 = Binance futures taker).

Usage: python3 trade_backtest.py BTCUSDT [lookback] [va_share]
"""
import sys
import numpy as np
import pandas as pd
from zone_breaks import run_engine


def load(sym, tf):
    return pd.read_csv(f"{sym}_{tf}.csv")


def build_trades(chart, events):
    o = chart["open"].to_numpy(); c = chart["close"].to_numpy()
    n = len(chart)
    trades, skipped = [], 0
    pend = {"up": None, "dn": None}

    for e in events:
        if e.kind in ("break_up", "break_dn"):
            side = "up" if e.kind == "break_up" else "dn"
            t_entry = e.t + 1
            if t_entry >= n:
                continue
            pend[side] = dict(direction=1 if side == "up" else -1,
                              zone=e.zone_id, t_break=e.t, t_entry=t_entry,
                              entry=o[t_entry], target=e.target, stop=e.stop)
        elif e.kind in ("valid_up", "invalid_up", "valid_dn", "invalid_dn"):
            side = "up" if e.kind.endswith("up") else "dn"
            tr = pend[side]; pend[side] = None
            if tr is None:
                continue
            if e.t < tr["t_entry"]:           # resolved on the break bar
                skipped += 1
                continue
            d = tr["direction"]
            if e.kind.startswith("valid"):
                # limit fill at target; if entry already beyond it, fill = entry
                exit_p = e.price if (tr["entry"] - e.price) * d < 0 else tr["entry"]
                outcome = "target"
            else:
                exit_p = e.price                # the invalidating close
                outcome = "stop"
            tr.update(t_exit=e.t, exit=exit_p, outcome=outcome,
                      ret=d * (exit_p - tr["entry"]) / tr["entry"],
                      bars_held=e.t - tr["t_entry"])
            trades.append(tr)

    for side in pend:                           # mark-to-market leftovers
        tr = pend[side]
        if tr is not None:
            d = tr["direction"]
            tr.update(t_exit=n - 1, exit=c[-1], outcome="open",
                      ret=d * (c[-1] - tr["entry"]) / tr["entry"],
                      bars_held=n - 1 - tr["t_entry"])
            trades.append(tr)
    return pd.DataFrame(trades), skipped


def stats(df, fee_bps_side=5.0):
    if len(df) == 0:
        return "  no trades"
    fee = 2 * fee_bps_side / 1e4
    net = df["ret"] - fee
    wins = df["ret"] > 0
    aw = df.loc[wins, "ret"].mean() if wins.any() else np.nan
    al = df.loc[~wins, "ret"].mean() if (~wins).any() else np.nan
    eq = float(np.prod(1 + net))
    lines = [
        f"  trades={len(df)}  win_rate={wins.mean():.1%}  "
        f"avg_win={aw:+.2%}  avg_loss={al:+.2%}  payoff={abs(aw/al):.2f}  "
        f"breakeven_wr={abs(al)/(abs(al)+aw):.1%}" if al and aw == aw else "",
        f"  expectancy/trade: gross={df['ret'].mean()*1e4:+.0f} bps, "
        f"net({fee_bps_side:.0f}bps/side)={net.mean()*1e4:+.0f} bps  "
        f"avg_hold={df['bars_held'].mean():.0f} bars",
        f"  compounded (1x, net): {eq - 1:+.1%} over period  "
        f"hit target={int((df.outcome=='target').sum())} "
        f"stopped={int((df.outcome=='stop').sum())} "
        f"open={int((df.outcome=='open').sum())}",
    ]
    return "\n".join(x for x in lines if x)


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    va = float(sys.argv[3]) if len(sys.argv) > 3 else 0.70
    qualify = len(sys.argv) > 4 and sys.argv[4] == "qualify"
    chart, ltf = load(sym, "1h"), load(sym, "5m")
    events, _, _, acc, rej = run_engine(chart, ltf, lookback, va, qualify=qualify)
    if qualify:
        print(f"[qualified sets only: accepted={acc}, rejected={rej}]")
    df, skipped = build_trades(chart, events)
    df["date"] = pd.to_datetime(chart["ts"].to_numpy()[df["t_entry"]], unit="ms")

    n = len(chart); split = int(n * 0.7)
    print(f"{sym} 1h  lookback={lookback} va={int(va*100)}%   "
          f"(skipped {skipped} same-bar resolutions)\n")
    for name, d in [("ALL", df),
                    ("LONGS", df[df.direction == 1]),
                    ("SHORTS", df[df.direction == -1]),
                    ("TRAIN (first 70%)", df[df.t_entry < split]),
                    ("TEST (last 30%)", df[df.t_entry >= split])]:
        print(name); print(stats(d)); print()

    print("By broken zone id (gross expectancy bps / n):")
    g = df.groupby(["direction", "zone"])["ret"].agg(["mean", "count"])
    for (d, z), r in g.iterrows():
        print(f"  {'long' if d==1 else 'short'} z{z}: "
              f"{r['mean']*1e4:+6.0f} bps  n={int(r['count'])}")
