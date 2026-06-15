#!/usr/bin/env python3
"""lab.py — the research-lab CLI. The single, guarded interface the daily Claude
Code agent (and humans) drive to research, create, modify, validate, and retire
trading strategies. The CLI — not discipline — enforces the cardinal rule: a
strategy cannot advance toward money without a passing leak test + walk-forward.

  lab list                      lifecycle + live state of every strategy
  lab status <name>             full manifest + experiment/version history + stats
  lab new <name> ...            scaffold a manifest + impl stub (research stage)
  lab tweak <name> --set k=v    edit signal params (logged; resets track record)
  lab promote <name>            advance lifecycle iff its gate passes
  lab retire <name> --reason    soft-retire (record kept); never hard-deletes
  lab lesson --idea ...         append a do-not-repeat row
  lab lessons                   print the do-not-repeat memory (read this first)
  lab leaktest|backtest|walkforward|gridsearch <name>   run the P1 harness

Designed to degrade gracefully: `list`/`status`/`lessons` work even if the DB is
unreachable or pandas isn't installed.
"""
import argparse
import json
import sys
import time

from rlab import registry

try:
    import db
except Exception as e:                                   # pragma: no cover
    db = None
    _DB_ERR = e


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _activity():
    if not db:
        return {}
    try:
        return db.activity()
    except Exception:
        return {}


def _resolved_stats(name):
    """Resolved paper/live count + hit rate + z for `name`, from the live log."""
    if not db:
        return {"n": 0, "hit": None, "z": None}
    try:
        bets, trades = db.stats(name)
    except Exception:
        return {"n": 0, "hit": None, "z": None}
    rows = bets + trades
    n = len(rows)
    if n == 0:
        return {"n": 0, "hit": None, "z": None}
    wins = sum(int(r.get("won") or 0) for r in rows)
    hit = wins / n
    z = (hit - 0.5) / (0.25 / n) ** 0.5
    return {"n": n, "hit": hit, "z": z}


def _passed_validation(name):
    """True if the ledger holds a passing leaktest AND a robust walkforward for
    the strategy's current params. The research->paper guard."""
    if not db:
        return False, "no DB"
    try:
        exps = db.experiments(name)
    except Exception as e:
        return False, f"DB error: {e}"
    leak = next((e for e in exps if e["kind"] == "leaktest"), None)
    wf = next((e for e in exps if e["kind"] == "walkforward"), None)
    if not leak or (leak.get("verdict") or "").lower() != "pass":
        return False, "no passing leaktest on record"
    if not wf or (wf.get("verdict") or "").lower() != "pass":
        return False, "no robust walkforward on record"
    return True, "leaktest + walkforward passed"


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_list(args):
    reg = registry.registry()
    act = _activity()
    print(f"{'name':<20} {'lifecycle':<15} {'kind':<10} {'venue':<12} "
          f"{'domain':<10} {'n':>5} {'pend':>5}  status")
    print("-" * 100)
    for name, m in reg.items():
        a = act.get(name, {})
        st = _resolved_stats(name)
        role = f" [{m['role']}]" if m.get("role") else ""
        print(f"{name:<20} {m.get('lifecycle','?'):<15} "
              f"{m.get('kind','?'):<10} {m.get('venue','?'):<12} "
              f"{m.get('domain','?'):<10} {st['n']:>5} {a.get('pending',0):>5}  "
              f"{m.get('status','')}{role}")


def cmd_status(args):
    m = registry.get(args.name)
    if not m:
        sys.exit(f"no such strategy: {args.name}")
    print(json.dumps(m, indent=2))
    st = _resolved_stats(args.name)
    if st["hit"] is not None:
        print(f"\nlive log: n={st['n']} hit={st['hit']:.1%} z={st['z']:+.1f}")
    else:
        print(f"\nlive log: n={st['n']} (none resolved yet)")
    ok, why = _passed_validation(args.name)
    print(f"validation: {'PASS' if ok else 'not yet'} — {why}")
    if db:
        for e in db.experiments(args.name, limit=5):
            print(f"  exp {e['kind']:<11} {e.get('verdict','')}  "
                  f"{time.strftime('%Y-%m-%d', time.localtime(e['ts']))}")
        for v in db.versions(args.name, limit=5):
            print(f"  ver {time.strftime('%Y-%m-%d', time.localtime(v['ts']))} "
                  f"{v.get('reason','')}")


IMPL_TEMPLATE = '''\
"""{name} — agent-authored strategy signal.

Contract: pure + as_of-indexed. The function receives a DataFrame of OHLCV (and,
for bracket kinds, the active zone bands) and must read ONLY the just-closed bar
and earlier — never the future. The harness guarantees this by construction; do
not defeat it. See strategies.py for reference implementations.
"""
import numpy as np


def signal(df, params=None):
    """Return for a {kind} strategy:
       binary/directional -> (side, rule) where side in {{'Up','Down',None}}
       bracket            -> dict(direction=+/-1, target, stop, rule) | None
    `params` is the manifest's signal.params dict (tunable / grid-searched)."""
    params = params or {{}}
    # TODO: implement the hypothesis:
    # {hypothesis}
    return (None, None)
'''


def cmd_new(args):
    import os
    name = args.name
    if os.path.exists(registry.manifest_path(name)):
        sys.exit(f"{name} already exists; edit its manifest or use `tweak`.")
    orders = [m.get("order", 0) for m in registry.manifests().values()]
    man = {
        "name": name,
        "order": (max(orders) + 1) if orders else 1,
        "label": args.label or name,
        "domain": args.domain,
        "kind": args.kind,
        "venue": args.venue,
        "status": "research (agent-authored)",
        "lifecycle": "research",
        "symbols": ",".join(args.symbols),
        "data": {"adapter": args.adapter, "symbols": args.symbols,
                 "timeframe": args.timeframe},
        "signal": {"module": f"rlab.impl.{name}", "fn": "signal",
                   "params": {}, "param_grid": {}},
        "exec_model": args.exec_model,
        "gate": dict(registry.DEFAULT_GATE),
        "method": args.hypothesis,
        "risk": "Paper only until leak test + walk-forward pass. Agent-authored.",
        "provenance": {"created_by": args.by, "date": time.strftime("%Y-%m-%d"),
                       "hypothesis": args.hypothesis,
                       "research_refs": args.refs},
    }
    registry.save_manifest(man)
    os.makedirs(registry.IMPL_DIR, exist_ok=True)
    open(os.path.join(registry.IMPL_DIR, "__init__.py"), "a").close()
    impl = os.path.join(registry.IMPL_DIR, f"{name}.py")
    if not os.path.exists(impl):
        with open(impl, "w") as f:
            f.write(IMPL_TEMPLATE.format(name=name, kind=args.kind,
                                         hypothesis=args.hypothesis))
    if db:
        try:
            db.record_experiment(name, "create", hypothesis=args.hypothesis,
                                  manifest=man, verdict="created", by_who=args.by)
        except Exception:
            pass
    print(f"created {registry.manifest_path(name)}")
    print(f"        {impl}")
    print("next: implement signal(), then `lab leaktest` + `lab walkforward`.")


def _coerce(v):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def cmd_tweak(args):
    m = registry.load_manifest(args.name)
    before = dict(m["signal"].get("params", {}))
    after = dict(before)
    for kv in args.set:
        if "=" not in kv:
            sys.exit(f"--set expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        after[k] = _coerce(v)
    m["signal"]["params"] = after
    # A tweak forces re-validation and resets the paper track record: the new
    # params have NOT earned the old strategy's credibility.
    m["lifecycle"] = "research"
    m["status"] = "research (re-validating after tweak)"
    registry.save_manifest(m)
    if db:
        try:
            db.record_version(args.name, before, after, reason=args.reason,
                              by_who=args.by)
        except Exception:
            pass
    print(f"tweaked {args.name}: {before} -> {after}")
    print("lifecycle reset to `research` — re-run leaktest + walkforward before "
          "it can affect money.")


def cmd_promote(args):
    m = registry.load_manifest(args.name)
    cur = m.get("lifecycle", "research")
    nxt = {"research": "paper", "paper": "live_candidate",
           "live_candidate": "live"}.get(cur)
    if not nxt:
        sys.exit(f"{args.name} is `{cur}` — nothing to promote to.")

    if cur == "research":
        ok, why = _passed_validation(args.name)
        if not ok and not args.force:
            sys.exit(f"BLOCKED research->paper: {why}. Run leaktest + "
                     f"walkforward first (or --force, discouraged).")
    elif cur == "paper":
        st = _resolved_stats(args.name)
        g = m.get("gate", registry.DEFAULT_GATE)
        if (st["n"] < g["min_resolved"] or (st["z"] or 0) < g["ev_z"]) \
                and not args.force:
            sys.exit(f"BLOCKED paper->live_candidate: have n={st['n']} "
                     f"z={st['z']}, need n>={g['min_resolved']} z>={g['ev_z']}.")
    elif cur == "live_candidate":
        # The single human checkpoint: real money requires an armed budget.
        import os
        if os.environ.get("LIVE_BUDGET_ARMED") != "1" and not args.force:
            sys.exit("BLOCKED live_candidate->live: no armed live budget. A "
                     "human must arm it (LIVE_BUDGET_ARMED=1 + caps) — P5.")

    registry.set_lifecycle(args.name, nxt, reason=args.reason, by_who=args.by)
    print(f"{args.name}: {cur} -> {nxt}")


def cmd_retire(args):
    if not args.reason:
        sys.exit("retire requires --reason (be sure before deleting).")
    m = registry.load_manifest(args.name)
    # Soft retire: record kept, never a hard delete. A live curve that
    # contradicts the stated reason should be surfaced, not silently retired.
    registry.set_lifecycle(args.name, "retired", reason=args.reason,
                           by_who=args.by)
    m2 = registry.load_manifest(args.name)
    m2["status"] = f"retired: {args.reason}"
    registry.save_manifest(m2)
    if db:
        try:
            db.record_lesson(f"retired {args.name}", domain=m.get("domain", ""),
                             verdict="retired", evidence=args.reason,
                             by_who=args.by)
        except Exception:
            pass
    print(f"{args.name} soft-retired (record + history kept). Reason: "
          f"{args.reason}")


def cmd_lesson(args):
    if not db:
        sys.exit(f"DB unavailable: {_DB_ERR}")
    db.record_lesson(args.idea, domain=args.domain, verdict=args.verdict,
                     evidence=args.evidence, redo_bar=args.redo_bar,
                     by_who=args.by)
    print(f"logged lesson: {args.idea} [{args.verdict}]")


def cmd_lessons(args):
    if not db:
        sys.exit(f"DB unavailable: {_DB_ERR}")
    rows = db.lessons()
    if not rows:
        print("(no lessons recorded yet)")
        return
    for r in rows:
        d = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
        print(f"{d}  [{r['verdict']:<9}] {r['idea']}")
        if r.get("evidence"):
            print(f"            evidence: {r['evidence']}")
        if r.get("redo_bar"):
            print(f"            revisit if: {r['redo_bar']}")


def _harness_cmd(kind):
    def run(args):
        from rlab import harness
        m = registry.load_manifest(args.name)
        fn = getattr(harness, kind)
        try:
            res = fn(m)
        except harness.HarnessPending as e:
            sys.exit(str(e))
        print(json.dumps(res, indent=2, default=str))
        if db:
            verdict = "pass" if res.get("passed") else "fail"
            db.record_experiment(args.name, kind, manifest=m, result=res,
                                  verdict=verdict, by_who=args.by)
    return run


# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--by", default="human", help="actor tag for the ledger")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    s = sub.add_parser("status"); s.add_argument("name")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("new")
    s.add_argument("name")
    s.add_argument("--domain", required=True)
    s.add_argument("--kind", required=True,
                   choices=["binary", "bracket", "directional"])
    s.add_argument("--venue", required=True)
    s.add_argument("--exec", dest="exec_model", required=True)
    s.add_argument("--adapter", required=True)
    s.add_argument("--symbols", required=True,
                   type=lambda x: [s.strip() for s in x.split(",")])
    s.add_argument("--timeframe", default="5m")
    s.add_argument("--label", default="")
    s.add_argument("--hypothesis", required=True)
    s.add_argument("--refs", default=[], type=lambda x: x.split(","))
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("tweak"); s.add_argument("name")
    s.add_argument("--set", action="append", required=True)
    s.add_argument("--reason", default="")
    s.set_defaults(func=cmd_tweak)

    s = sub.add_parser("promote"); s.add_argument("name")
    s.add_argument("--reason", default=""); s.add_argument("--force",
                                                           action="store_true")
    s.set_defaults(func=cmd_promote)

    s = sub.add_parser("retire"); s.add_argument("name")
    s.add_argument("--reason", required=True)
    s.set_defaults(func=cmd_retire)

    s = sub.add_parser("lesson")
    s.add_argument("--idea", required=True)
    s.add_argument("--domain", default="")
    s.add_argument("--verdict", default="rejected")
    s.add_argument("--evidence", default="")
    s.add_argument("--redo-bar", dest="redo_bar", default="")
    s.set_defaults(func=cmd_lesson)

    sub.add_parser("lessons").set_defaults(func=cmd_lessons)

    for k in ("leaktest", "backtest", "walkforward", "gridsearch"):
        sp = sub.add_parser(k); sp.add_argument("name")
        sp.set_defaults(func=_harness_cmd(k))

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
