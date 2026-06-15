"""rlab — the autonomous strategy research lab.

Package layout:
  registry.py    manifest loader + lifecycle + legacy merge (source of truth for
                 the *machine* fields: domain/data/signal/exec_model/gate/...)
  harness.py     leak-safe backtest + walk-forward + grid search (the R&D engine)
  registry/*.json   one manifest per strategy (CRUD = create/edit/delete a file)
  impl/*.py      agent-authored signal modules for NEW strategies

The root-level `lab.py` is the CLI that drives this package and enforces the
lifecycle guards (cardinal rule: nothing advances without leak + walk-forward).
"""
