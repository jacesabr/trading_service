# daily_run.md — the major strategy-analysis run (manual, via Claude Code)

This is the **main strategy-analysis runbook** (companion to
`tradingview_automation_run.md`, which handles community ideas). Run it **once a day
by hand in Claude Code**: the heavy research/validation pass — distinct from the
deterministic `daily.py` cron on Render (every 2h, no LLM) that places/resolves the
REAL broker orders on its own.

> **Current state (2026-06-17):** execution is on **real broker demos** now, not
> local sim — crypto/gold → **Bybit demo**, US equities → **Alpaca paper**. The
> self-resolved sim lab is **retired** (sim data deleted; `LAB_SIM` off; the
> `signal-runner` worker suspended). A validated strategy only produces results once
> it's **wired to a real demo venue** — a backtest/leaktest pass is necessary but no
> longer sufficient. **Every run ends with the AUDIT** (step 4) so our recorded
> results can't drift from the brokers'.

> **Research broadly — do NOT default to zone/gap trading.** The current registry is
> heavily zone/gap-based (`gaptrav*`, `far_targets`, `zone_break*`, gap×meanrev) —
> a historical bias, not a conclusion. The framework is **signal-agnostic**: a
> strategy is just a manifest + an arbitrary signal fn; nothing constrains it to
> zones. Each run, deliberately explore DIFFERENT families — momentum/trend,
> mean-reversion, breakout, volatility / vol-targeting, carry, order-flow &
> microstructure, sentiment/news, event-driven, seasonality, statistical/ML,
> cross-asset/relative-value. Let the data (and the community-ideas track record)
> decide; don't keep building variations of one idea.

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

4. **Audit (mandatory — verify results against the brokers).** Our numbers must
   match the venue's truth, not our own bookkeeping. The TradingView resolve step
   (`python tradingview_ideas.py run`) and `daily.py` both run `ideas.execute.audit()`
   automatically, which reconciles every open/pending idea against Bybit/Alpaca and
   prints `[audit] ✓ N match` or flags each mismatch. For the strategy battery,
   `equity_orders.resolve_open()` reconciles bracket fills from Alpaca. **Run a
   resolve cycle and confirm the audit is clean** before shipping; investigate any
   `⚠ DISAGREE` line (it means a recorded result drifted from the broker).
   ```
   python tradingview_ideas.py run     # places/fills/resolves + prints the audit
   ```

5. **Ship it.** Commit and push to `master`, then trigger the Render deploys so new
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
