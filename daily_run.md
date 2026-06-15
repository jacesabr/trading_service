# daily_run.md — the daily research-lab run (manual, via Claude Code)

Run this **once a day by hand in Claude Code**. It is the heavy analysis/research
pass — distinct from the deterministic `daily.py` data cron on Render (every 2h,
no LLM) that keeps paper positions recording/resolving on its own.

You operate the lab through `lab.py` and the adapters. NVIDIA NIM is the free
in-code/runtime LLM; **Firecrawl + Tavily** (or built-in web search) do research +
prior-art checks. **Paper only — never real money** (the live path is gated off by
design). Reads/writes the live Neon DB when `DATABASE_URL` is set (else local
SQLite). Crypto data is keyless via `data.py`; Kalshi/Alpaca adapters use the keys
in `.env`.

## Every run, in order

0. **Orient.** Read `docs/INFRA.html` (the living architecture doc) and run
   `python lab.py lessons` — do not re-litigate settled-dead ideas. Run
   `python daily.py` so open Kalshi/equity positions resolve and new ones record.

1. **Pick a focus** (rotate; don't do all three every day):

   **A — New research.** Find recent/novel signal ideas in any domain (crypto,
   prediction markets, options, weather, equities) via Firecrawl/Tavily/web search.
   Prior-art check each ("has anyone done this? arbed away?"). For survivors:
   `lab new …` → implement the signal in `rlab/impl/<name>.py` (pure, as_of-indexed
   — the harness enforces no-lookahead) → `lab leaktest` (fail ⇒ discard +
   `lab lesson`) → `lab gridsearch` + `lab walkforward` (not robust ⇒ discard +
   `lab lesson`) → it registers as **paper**.

   **B — Ongoing research.** Deepen `research`/`paper` strategies: more data,
   refined grids, more instruments/regimes. For live-collected strategies (e.g.
   `kalshi_crypto_model`), evaluate accumulated predictions vs settlement
   (calibration, edge vs market-implied).

   **C — Tweak / adjust / retire.** Detect drift on running strategies.
   `lab tweak <name> --set k=v --reason …` (resets the track record → re-validate).
   **Be sure before deleting:** `lab retire --reason` is soft and evidence-gated; a
   live curve that contradicts the stated reason is surfaced, not retired. Never
   hard-delete a strategy or its history.

2. **Record outcomes.** Every rejection → `lab lesson`. Findings live in the ledger
   (`experiments` / `strategy_versions` / `lessons`).

3. **Upkeep (mandatory).** If you changed any infrastructure — a component, adapter,
   table, route, env var, the lifecycle/gate — **update `docs/INFRA.html` in the
   same change and append a dated Changelog line.** Keep build-status tags honest
   (`built` only when it runs and is verified). This is what keeps the system real
   instead of drifting into undocumented sprawl.

4. **Ship it.** Commit and push to `master`, then trigger the Render deploys so new
   strategies show on the dashboard:
   ```
   curl -s -X POST -H "Authorization: Bearer $RENDER_API_KEY" \
     "https://api.render.com/v1/services/srv-d8ncr4k8aovs73ab7bb0/deploys" -d '{}'  # dashboard
   curl -s -X POST -H "Authorization: Bearer $RENDER_API_KEY" \
     "https://api.render.com/v1/services/crn-d8o1svjeo5us738btld0/deploys" -d '{}'  # daily cron
   ```
   New strategies appear as paper cards and start collecting real numbers. Most
   runs correctly reject most ideas — disciplined rejection, recorded in `lessons`,
   IS the product, not a failed run.

## Hard floors (never cross)
- **Validity:** nothing reaches `paper` without a passing leak test + walk-forward.
- **Money:** no real capital. The live path refuses unless someone set
  `LIVE_BUDGET_ARMED=1` — leave it off.

## Useful commands
```
python daily.py                      resolve + collect (Kalshi + equities)
python lab.py list                   lifecycle + P&L state of every strategy
python lab.py status <name>          full manifest + ledger + live stats
python lab.py lessons                do-not-repeat memory (read first)
python lab.py new|leaktest|backtest|walkforward|gridsearch|tweak|promote|retire
python kalshi_paper.py --probe       preview Kalshi model predictions
python equity_paper.py --probe       preview equity signals
```
