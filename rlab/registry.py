"""rlab.registry — strategy manifests, loading, lifecycle, and legacy merge.

A strategy is a self-describing object. The *machine* fields live in one JSON
manifest per strategy under rlab/registry/*.json:

    name        unique id (matches the filename stem)
    order       sort order for display
    domain      crypto | prediction_market | crypto_options | weather | equity | ...
    kind        binary | bracket | directional
    venue       polymarket | spot | kalshi | deribit | alpaca | ...
    lifecycle   research | paper | live_candidate | live | retired
    role        (optional) "control" for settled-negative paper controls
    data        {adapter, symbols:[...], timeframe}
    signal      {module, fn, params:{...}, param_grid:{...}}
    exec_model  spot_bps | bracket_sltp | polymarket_binary | kalshi_binary |
                options_mid | alpaca_equity
    gate        promotion thresholds {min_resolved, live_within_pp, ev_z}
    provenance  {created_by, date, hypothesis, research_refs:[...]}

The human-facing fields (label/status/symbols/method/risk) for the original 8
strategies still live in strategies.STRATEGIES; manifests for those carry only
the machine fields and are MERGED on top. NEW (agent-authored) strategies carry
every field in the manifest, so the registry needs no code change to add one —
just drop a manifest + impl module. That is the whole point.

Loading is import-cycle-safe: this module imports strategies (for the legacy
dict) but strategies must NOT import this module.
"""
import glob
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_DIR = os.path.join(HERE, "registry")
IMPL_DIR = os.path.join(HERE, "impl")

LIFECYCLES = ("research", "paper", "live_candidate", "live", "retired")

# Default promotion gate (bars defined in daily_run.md — the single source of truth).
DEFAULT_GATE = {"min_resolved": 400, "live_within_pp": 2.0, "ev_z": 2.0}


def manifest_path(name):
    return os.path.join(REGISTRY_DIR, f"{name}.json")


def load_manifest(name):
    with open(manifest_path(name)) as f:
        return json.load(f)


def save_manifest(man):
    """Write a manifest back to disk (atomic-ish). The single mutation point so
    every CRUD op is a file write the agent/human can diff and git-track."""
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    man.setdefault("name", man["name"])
    tmp = manifest_path(man["name"]) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(man, f, indent=2, sort_keys=False)
    os.replace(tmp, manifest_path(man["name"]))


def manifests():
    """All manifests as {name: dict}, sorted by 'order' then name."""
    out = {}
    for p in glob.glob(os.path.join(REGISTRY_DIR, "*.json")):
        try:
            man = json.load(open(p))
        except Exception as e:
            print(f"  [registry] skip {os.path.basename(p)}: {e}")
            continue
        man.setdefault("name", os.path.splitext(os.path.basename(p))[0])
        out[man["name"]] = man
    return dict(sorted(out.items(),
                       key=lambda kv: (kv[1].get("order", 999), kv[0])))


def _legacy():
    """The original 7-key human-facing dicts. Imported lazily to avoid a cycle
    and to degrade gracefully if strategies.py can't import (e.g. no pandas)."""
    try:
        import strategies
        return dict(strategies.STRATEGIES)
    except Exception as e:
        print(f"  [registry] legacy STRATEGIES unavailable: {e}")
        return {}


def registry():
    """The merged full view: legacy human fields + manifest machine fields, plus
    any manifest-only (new) strategies. This is what runner/dashboard/lab read."""
    legacy = _legacy()
    mans = manifests()
    full = {}
    for name, man in mans.items():
        base = dict(legacy.get(name, {}))
        base.update(man)                       # manifest augments / defines
        base.setdefault("gate", dict(DEFAULT_GATE))
        full[name] = base
    for name, leg in legacy.items():           # legacy entries without a manifest
        if name not in full:
            d = dict(leg)
            d["name"] = name
            d.setdefault("lifecycle", _infer_lifecycle(leg))
            d.setdefault("gate", dict(DEFAULT_GATE))
            full[name] = d
    return full


def get(name):
    return registry().get(name)


def legacy_view():
    """Projection back to the 7 legacy keys, for any caller that only wants the
    original shape. Currently runner.py/dashboard_db.py read strategies.STRATEGIES
    directly; they switch to registry() in P2 to pick up new strategies."""
    keys = ("label", "kind", "venue", "status", "symbols", "method", "risk")
    return {n: {k: v[k] for k in keys if k in v} for n, v in registry().items()}


def _infer_lifecycle(leg):
    s = (leg.get("status") or "").lower()
    if "live candidate" in s:
        return "live_candidate"
    if s.startswith("live"):
        return "live"
    if "retired" in s:
        return "retired"
    return "paper"


def set_lifecycle(name, lifecycle, reason="", by_who="human"):
    """Move a strategy's lifecycle. Validation/gate enforcement lives in lab.py;
    this is the low-level setter that records the transition timestamp."""
    if lifecycle not in LIFECYCLES:
        raise ValueError(f"unknown lifecycle {lifecycle!r}; pick {LIFECYCLES}")
    man = load_manifest(name)
    prev = man.get("lifecycle")
    man["lifecycle"] = lifecycle
    man.setdefault("history", []).append(
        {"ts": int(time.time()), "from": prev, "to": lifecycle,
         "reason": reason, "by": by_who})
    save_manifest(man)
    return prev, lifecycle
