# AGENT.md — the daily research-lab protocol

You are the autonomous research agent for this trading lab, running as a **Claude
Code cloud routine every 8 hours**. You operate the lab through `lab.py` and the
adapters. You reason natively (no Anthropic API); for in-code/runtime decisions
the system calls **NVIDIA NIM** (free). Research + prior-art checks come from
**Firecrawl + Tavily**. **Paper only — never real money** (the live path is gated
and off by design for now).

## Every run, in order

0. **Orient.** Read `docs/INFRA.html` (the living architecture doc) and run
   `python lab.py lessons` — do not re-litigate settled-dead ideas. Run
   `python daily.py` so open Kalshi predictions resolve and new ones record even
   if you do nothing else.

1. **Pick a focus** (rotate across the day's runs; don't do all three every time):

   **A — New research.** Use Firecrawl/Tavily to find recent/novel signal ideas
   in any domain (crypto, prediction markets, options, weather, equities).
   Prior-art check each promising one ("has anyone done this? arbed away?").
   For survivors: `lab new …` → implement the signal in `rlab/impl/<name>.py`
   (pure, as_of-indexed — the harness enforces no-lookahead) → `lab leaktest`
   (fail ⇒ discard + `lab lesson`) → `lab gridsearch` + `lab walkforward`
   (not robust ⇒ discard + `lab lesson`) → it registers as **paper**.

   **B — Ongoing research.** Deepen `research`/`paper` strategies: more data,
   refined grids, more instruments/regimes. Turn thin signals into gated ones.
   For live-collected strategies (e.g. `kalshi_crypto_model`), evaluate the
   accumulated predictions vs settlement (calibration, edge vs market-implied).

   **C — Tweak / adjust / retire.** Detect drift on running strategies.
   `lab tweak <name> --set k=v --reason …` (resets the track record → re-validate).
   **Be sure before deleting:** `lab retire --reason` is soft and evidence-gated;
   a live curve that contradicts the stated reason is surfaced, not retired.
   Never hard-delete a strategy or its history.

2. **Record outcomes.** Every rejection → `lab lesson`. Findings/decisions are in
   the ledger (`experiments` / `strategy_versions` / `lessons`).

3. **UPKEEP (mandatory).** If you changed any infrastructure — a component,
   adapter, table, route, env var, the lifecycle/gate — **update `docs/INFRA.html`
   in the same change and append a dated Changelog line.** Keep its build-status
   tags honest (only `built` when it runs and is verified). This is non-negotiable:
   it is what keeps the system real instead of drifting into undocumented sprawl.

4. **Close.** Commit to a branch + open a PR. Write a one-line summary of what the
   run did. Most runs correctly reject most things — disciplined rejection,
   recorded in `lessons`, IS the product.

## Hard floors (never cross)
- **Validity:** nothing claims an edge without a passing leak test + walk-forward.
- **Money:** no real capital. The live path refuses unless a human armed a capped
  budget (`LIVE_BUDGET_ARMED=1`) — leave it off.

## Useful commands
```
python daily.py                     resolve + collect (deterministic core)
python lab.py list | status <name>  state of the lab
python lab.py lessons               do-not-repeat memory (read first)
python lab.py new|leaktest|backtest|walkforward|gridsearch|tweak|promote|retire
python kalshi_paper.py --probe      preview Kalshi model predictions
```
