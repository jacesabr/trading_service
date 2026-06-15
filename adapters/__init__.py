"""adapters — per-domain data + execution layers behind a uniform contract.

data/<name>.py  : fetch(symbols, tf, start, end) for backtest, recent(...) live
exec/<name>.py  : paper_open(signal, market_state) + resolve(position, future)

Keeps the core (registry/harness/runner) asset- and venue-agnostic.
"""
