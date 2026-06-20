# CLAUDE.md — project guide for Claude Code

**Single source of truth → [daily_run.md](daily_run.md)** (browser twin
`daily_run.html`, served at `/docs`). Read it first. It holds the thesis, every
rule, the architecture, the current roster, the research procedure, the
infra/deploy reference, and the do-not-relitigate lessons. **Do not duplicate any
of that here** — if a rule or plan changes, change it in daily_run (`.md` + `.html`
together, kept in sync).

## the one rule that must never be forgotten
**NO LOOKAHEAD** — every signal must be computable from data available *before* the
outcome bar; the harness enforces it. Write the leak test first, always.

## operating notes (Claude-Code-specific; not in daily_run)
- Full autonomy in this repo: commit / push / deploy without pausing. The
  **paper-only floor still holds** — never set `LIVE_BUDGET_ARMED=1`.
- The active mandates (≤5m · API-testable-or-deleted · verified broker data only ·
  diverse families / no duplicates · Kalshi not Polymarket) are defined in
  daily_run — defer to it, don't restate.
- After ANY infra / roster / rule change: update `daily_run.md` **and**
  `daily_run.html` in the same change, then deploy (dashboard for UI/manifests,
  cron `signal-daily` for the batteries). Auto-deploy is unreliable — POST to the
  Render deploys API with `RENDER_API_KEY`.
