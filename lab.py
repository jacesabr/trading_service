"""lab.py — the leak-safe research CLI (lean rebuild 2026-06-21).

The single interface for researching and testing strategies. Every idea proves
out on HELD-OUT data, leak-free, BEFORE it can run live.

  python lab.py list                       lifecycle of every strategy
  python lab.py status <name>              manifest + recent ledger
  python lab.py lessons                    do-not-repeat memory (READ FIRST)
  python lab.py new <name> --venue bybit_demo --symbols BTCUSDT,ETHUSDT
                                           scaffold manifest + rlab/impl/<name>.py
  python lab.py leaktest <name>            held-out TEST hit, leak-free (the GATE)
  python lab.py backtest <name>            TRAIN/VAL/TEST hit + edge vs baseline
  python lab.py walkforward <name>         rolling OOS windows (robust = >=4/5)
  python lab.py gridsearch <name>          sweep param_grid on VALIDATION
  python lab.py lesson "<idea>" --evidence "..." [--redo "..."]
  python lab.py retire <name> --reason "..."

Mandates (see daily_run.md — the single source of truth):
  * timeframe <=5m
  * a strategy ships only if it runs on a REAL broker/exchange API (Bybit demo /
    Alpaca paper / Kalshi demo) — no self-resolved paper, no simulators
  * keep families DIVERSE — no near-duplicate signals
  * paper only; the live-money floor is off by design
"""
import argparse
import json
import os
import sys

import db
from rlab import registry, harness

IMPL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rlab", "impl")

IMPL_STUB = '''"""{name} — leak-free signal. PURE: given a trailing window of bars ending at
the just-closed bar t, return the prediction for the NEXT bar t+1:
  +1 (up) | -1 (down) | 0 (no signal)
The harness scopes/lags for you — NEVER read beyond the last row of `df`."""
import numpy as np


def signal(df, params=None):
    params = params or {}
    c = df["close"].to_numpy()          # columns: ts,open,high,low,close,volume
    if len(c) < 30:
        return 0
    # TODO: your edge here. Must be computable from `df` only (no future bars).
    return 0
'''


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def cmd_list(a):
    reg = registry.registry()
    print(f"{'NAME':28} {'LIFECYCLE':14} {'DOMAIN':18} {'VENUE':12} KIND")
    for name, m in reg.items():
        print(f"{name:28} {m.get('lifecycle',''):14} {m.get('domain',''):18} "
              f"{m.get('venue',''):12} {m.get('kind','')}")
    print(f"\n{len(reg)} strategies.")


def cmd_status(a):
    m = registry.get(a.name)
    if not m:
        sys.exit(f"no such strategy: {a.name}")
    _print(m)
    exps = db.experiments(a.name, limit=8)
    print(f"\n--- recent experiments ({len(exps)}) ---")
    for e in exps:
        print(f"  {e['kind']:12} {e.get('verdict','')}  {e.get('hypothesis','')[:70]}")


def cmd_lessons(a):
    ls = db.lessons()
    print(f"--- lessons / do-not-repeat ({len(ls)}) ---")
    for x in ls:
        print(f"  [{x['verdict']}] {x['idea']}  — {x.get('evidence','')[:80]}")
        if x.get("redo_bar"):
            print(f"        revisit if: {x['redo_bar']}")


def cmd_new(a):
    if registry.get(a.name) or os.path.exists(registry.manifest_path(a.name)):
        sys.exit(f"{a.name} already exists")
    if a.venue not in ("bybit_demo", "alpaca", "kalshi"):
        sys.exit("--venue must be a REAL API venue: bybit_demo | alpaca | kalshi "
                 "(API-testability mandate; no sim/self-resolved venues)")
    man = {
        "name": a.name, "order": 30,
        "label": a.label or f"{a.name} · {a.tf} · {a.domain}",
        "domain": a.domain, "kind": "directional", "venue": a.venue,
        "lifecycle": "research", "role": "data-collection",
        "data": {"adapter": a.domain, "symbols": a.symbols.split(","),
                 "timeframe": a.tf},
        "signal": {"module": f"rlab.impl.{a.name}", "fn": "signal",
                   "window": 300, "params": {}, "param_grid": {}},
        "exec_model": "directional_next_bar",
        "gate": dict(registry.DEFAULT_GATE),
        "provenance": {"created_by": "agent", "date": a.date or "",
                       "hypothesis": a.hypothesis or "TODO",
                       "research_refs": []},
    }
    registry.save_manifest(man)
    os.makedirs(IMPL_DIR, exist_ok=True)
    impl = os.path.join(IMPL_DIR, f"{a.name}.py")
    if not os.path.exists(impl):
        with open(impl, "w") as f:
            f.write(IMPL_STUB.format(name=a.name))
    print(f"created rlab/registry/{a.name}.json + rlab/impl/{a.name}.py")
    print("next: implement signal(), fetch data (python data.py ...), then "
          f"python lab.py leaktest {a.name}")


def _record(name, kind, res):
    try:
        db.record_experiment(name, kind, result=res,
                             verdict=("pass" if res.get("passed") else "fail"))
    except Exception as e:
        print(f"  (ledger write skipped: {e})")


def cmd_leaktest(a):
    m = registry.get(a.name) or sys.exit(f"no such strategy: {a.name}")
    res = harness.leaktest(m)
    _print(res); _record(a.name, "leaktest", res)
    print("PASS — leak-free tilt" if res.get("passed")
          else "FAIL — log a lesson and move on (python lab.py lesson ...)")


def cmd_backtest(a):
    m = registry.get(a.name) or sys.exit(f"no such strategy: {a.name}")
    res = harness.backtest(m); _print(res); _record(a.name, "backtest", res)


def cmd_walkforward(a):
    m = registry.get(a.name) or sys.exit(f"no such strategy: {a.name}")
    res = harness.walkforward(m); _print(res); _record(a.name, "walkforward", res)


def cmd_gridsearch(a):
    m = registry.get(a.name) or sys.exit(f"no such strategy: {a.name}")
    res = harness.gridsearch(m); _print(res); _record(a.name, "gridsearch", res)


def cmd_lesson(a):
    db.record_lesson(a.idea, domain=a.domain, verdict=a.verdict,
                     evidence=a.evidence, redo_bar=a.redo)
    print(f"logged lesson: [{a.verdict}] {a.idea}")


def cmd_retire(a):
    if not a.reason:
        sys.exit("--reason required to retire")
    prev, now = registry.set_lifecycle(a.name, "retired", reason=a.reason,
                                       by_who="human")
    print(f"{a.name}: {prev} -> {now}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(fn=cmd_list)
    for c in ("status", "leaktest", "backtest", "walkforward", "gridsearch"):
        sp = sub.add_parser(c); sp.add_argument("name")
        sp.set_defaults(fn=globals()[f"cmd_{c}"])
    sub.add_parser("lessons").set_defaults(fn=cmd_lessons)

    sp = sub.add_parser("new"); sp.set_defaults(fn=cmd_new)
    sp.add_argument("name")
    sp.add_argument("--venue", required=True, help="bybit_demo | alpaca | kalshi")
    sp.add_argument("--domain", default="crypto")
    sp.add_argument("--tf", default="5m")
    sp.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    sp.add_argument("--label", default=""); sp.add_argument("--hypothesis", default="")
    sp.add_argument("--date", default="")

    sp = sub.add_parser("lesson"); sp.set_defaults(fn=cmd_lesson)
    sp.add_argument("idea")
    sp.add_argument("--domain", default=""); sp.add_argument("--verdict", default="rejected")
    sp.add_argument("--evidence", default=""); sp.add_argument("--redo", default="")

    sp = sub.add_parser("retire"); sp.set_defaults(fn=cmd_retire)
    sp.add_argument("name"); sp.add_argument("--reason", default="")

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
