# tradingview_automation_run.md — the TradingView Ideas run (1–2× daily, via Claude Code)

Run this **once or twice a day by hand in Claude Code**. It scrapes community
trading ideas off TradingView, reads each idea's chart with **Claude Code's own
vision** (no API key yet — *you*, the agent in this session, are the vision
model), records a structured trade, and shows everything on the dashboard's
**TradingView Ideas** board.

Companion to `daily_run.md` (the strategy-lab research run). That one researches
signals; this one turns the crowd's published ideas into a scored, demo-executed
track record. **Paper / demo only** — the live path stays gated off.

> **Execution is on REAL broker demos now (2026-06-17).** Crypto + gold ideas →
> **Bybit demo** (broker-held TP/SL, long+short); US-equity ideas → **Alpaca paper**
> bracket. `binance_sim` is only a dev fallback when Bybit is unarmed. The VISION
> step is still **manual** (Claude Code reads the charts) — the VLM path is wired
> (`ANTHROPIC_API_KEY`/`NVIDIA_API_KEY` → `ideas/scrape.py`) but stays manual until
> the ideas prove edge. Keys live in `.env` (Tavily, Alpaca, Bybit demo).

---

## What's built (state — verified 2026-06-16)
- **`ideas/scrape.py`** — scrape + extract + store. Pulls 4 TradingView listing
  feeds via **Tavily** (`/extract`), parses each idea's symbol / full analysis /
  author / comments straight from the listing markdown, derives the **chart image
  URL from the slug** (`<id>` → `https://s3.tradingview.com/<c>/<id>_big.png`, no
  page fetch), text-extracts direction + any in-text price levels, and stores rows
  in the **`ideas`** table. Enforces the **50 open-trade cap** (skips scraping at
  cap). **Timeframe-agnostic** — every TF is accepted. Scrapes a wide set of feeds
  (crypto/stocks/forex/indices/futures + popular/recent/editors'-picks + extra pages).
- **dashboard** — `/api/ideas` + a **TradingView Ideas** board at the top of the
  main page (chart thumbnail, symbol, dir, entry/target/stop, TF, basis, conf,
  status, outcome, author, boosts, link).
- **`ideas/execute.py`** — P3 demo execution. Routes each `extracted` idea by asset:
  - **crypto + gold** (XAUUSD→PAXGUSDT) → **Bybit demo** (`bybit_orders.py`): a REAL
    LIMIT entry at the author's price (hedge mode), then broker-held reduce-only
    TP/SL for that idea's qty — long **and** short, many ideas per symbol. Broker is
    the source of truth (fills + P&L from Bybit). `binance_sim` only if Bybit unarmed.
  - **US equities** → **Alpaca paper**: a REAL limit-entry **bracket (OCO)** order
    — the broker rests the entry and holds the TP/SL exit itself (genuine fill,
    long+short). Fills at the next market open if placed while closed.
  - FX/metals/unsupported → `no_venue` (tracked, not executed).
- **status lifecycle:** `needs_vision` → (chart read) → `extracted`
  → (`run`) → `pending` → `open` → `resolved` (target/stop/flat). Side paths:
  `invalidated` (market moved past the level), `no_venue`, `expired`.
- **Not built yet (P4+):** engagement analytics (does boosts/author predict
  win-rate). A real-fill venue for crypto shorts (Kraken paper-futures / Bybit
  testnet) is the upgrade beyond `binance_sim`; gold/FX could use Capital.com.

---

## The run, in order

### 0. Orient
```bash
python tradingview_ideas.py show    # current ideas table + statuses
```
Set the scraper key for this shell (local; on Render it's a service env var):
```bash
export TAVILY_API_KEY=tvly-...      # from .env
```

### 1. Scrape new ideas
```bash
python tradingview_ideas.py scrape --limit 10   # scrape up to 10 NEW ideas, store them
```
- At the **20-open cap** it prints `[cap] … skipping scrape` and stops — that's
  the compute governor working, not an error.
- Each new idea lands as `needs_vision` (text-only) unless its levels were
  already in the post text (rare) → `extracted`.
- `--probe` does a dry run (scrape + extract, **no DB writes**) if you just want
  to look.

### 2. Read the charts (Claude Code vision — DO THIS AUTOMATICALLY, don't ask)
**This is a standing instruction: when running this automation, read every
`needs_vision` chart yourself with the `Read` tool and write the levels back — do
not pause to ask the user.** It is the core of the job, not an optional step.
Aim to clear the whole `needs_vision` queue each run so the book stays near 50.

List what needs a chart read:
```bash
python tradingview_ideas.py vision  # JSON: id, symbol, chart_image_url, thesis
```
For **each** idea in that list, in this Claude Code session:
1. **Download** the chart image (the URL is in the JSON):
   ```bash
   mkdir -p _charts
   curl -s -o _charts/<id>.png "<chart_image_url>"
   ```
   (PowerShell: `Invoke-WebRequest -Uri "<url>" -OutFile _charts\<id>.png`)
2. **Read it** with the `Read` tool — look for the *drawn* trade:
   - the symbol + **timeframe** in the chart header (top-left),
   - horizontal **entry / target / stop** lines and their price labels,
   - the **projected path** arrow → long vs short,
   - zones / FVG / fib levels.
3. **Write the levels back:**
   ```bash
   python tradingview_ideas.py set <id> \
     --tf 4h --direction -1 \
     --entry 67000 --target 58000 --stop 68200 --confidence 0.65
   ```
   - `--basis chart` is the default (it's a real author level read off the chart).
   - **Any timeframe is tradeable** — record the chart's real TF (`5m`…`1w`); it
     sets the max-hold, never drops the idea.
   - **Educational / no-setup infographics** (no real levels) → leave as
     `needs_vision`, or set `--direction 0` to flag it unclear.
   - **Thesis-only, no drawn levels** → *generate* a sane bracket from the thesis
     + the visible price + structure, and tag it `--basis generated` so it's never
     confused with the author's own call.

### 2.5 Stop & target discipline (READ THIS — it is where we were losing money)
**Every resolved loss to date came from sloppy level-setting at this step, not bad
market calls** (see `docs/TRADE_AUDIT.md`). The `$NOW` weekly long was recorded a
−210bps "stop" though price never broke the author's trendline — because the stop
was put at an arbitrary number 1.5% under a marketable fill instead of below the
structural support. Follow these rules; they are hard rules, not suggestions.

**Read the trade the AUTHOR drew — do not invent levels.**
- The stop goes **beyond the structural invalidation the author drew**, not a round
  number near the entry. For a LONG: just **below the local support / demand zone /
  the trendline whose break kills the thesis** (a few ticks past it so a wick
  doesn't trip it). For a SHORT: just **above the local resistance / the swing
  high**. Find the *nearest* swing low/high or zone edge that price must hold for
  the idea to remain valid, and place the stop the far side of it.
- The target goes at the **next opposing structure** (resistance for a long, support
  for a short) the author is aiming at — read it off the chart, don't extrapolate.
- **The idea must NOT already be played out.** If price has already reached the
  target, or already broken past the stop level, the setup is dead → leave it
  `needs_vision`/skip; do not set levels. ("Price never broke the trendline" must be
  true *at entry*, or there is no trade.)
- **Entry vs live price decides the order, and we only place two kinds:**
  - **Pullback / limit entry** — long entry *below* live price, short entry *above*
    it. Good: it rests and fills exactly at the level.
  - **Breakout entry** — long entry *above* live, short entry *below*. ⚠ A bracket
    cannot rest a stop-entry (Alpaca rejects it), so a limit there fills *immediately
    at the current price*, not at the breakout. Only set a breakout entry if the
    **current price** already gives a valid trade (stop still structurally beyond the
    live price, with timeframe-appropriate room). Otherwise set the entry **at/just
    inside the live price** so it's an honest pullback entry, and keep the same
    structural stop/target.
- **Timeframe-appropriate stop distance (floor).** A stop tighter than this for the
  timeframe is noise and will be auto-**invalidated** by `execute.py` — widen it to
  real structure or skip: 5m ≈ 0.3%, 15m ≈ 0.5%, 30m ≈ 0.7%, 1h ≈ 1%, 4h ≈ 2%,
  1d ≈ 3.5%, **1w ≈ 6%**, 1M ≈ 10% (of price). A weekly idea with a 1–2% stop is
  always wrong.
- **Account for broker price drift.** Our fill is on Bybit/Alpaca, not TradingView's
  feed — expect the live price to differ from the author's chart by tenths of a
  percent to a couple percent, and on illiquid names more. Never set a stop so close
  that this drift alone stops you. Bybit re-anchors TP/SL to the real fill
  (RR preserved); Alpaca uses the **absolute** stop you set, so on equities the stop
  must already be far enough from the *current* price, not just from the entry.
- **Drift tolerance, in units of risk R** (R = entry→stop distance). Trivial drift is
  fine, but if the fill lands too far from the author's entry the published setup is
  stale — it's no longer the trade. **Cutoff: 1 R lost to drift.** The gate
  auto-**invalidates** when the fill is more than **1.0 R** off the entry. If the live
  price is already > 1 R away from the entry when you go to `set`, don't bother — the
  level has moved on.

**Write a justification box with every `set`** so the trade is auditable. Put it in
the commit message / run notes for that idea, in this format:

```
┌─ #<id> <SYMBOL> · <LONG|SHORT> · <tf> · <venue> ─────────────────────────┐
│ live px: <price at the time you set levels>                              │
│ entry  <E>  — <why: at the trendline / pullback to demand / current px>  │
│ stop   <S>  — <which support/resistance it sits beyond, + % from entry>  │
│ target <T>  — <which opposing structure it aims at>                       │
│ RR: <(T−E)/(E−S)>   invalidation: <the line/zone whose break kills it>   │
│ entry type: <resting limit | marketable-now> ; drift checked: <yes/px>   │
└──────────────────────────────────────────────────────────────────────────┘
```

The `run` step (below) enforces a machine version of these rules: `_entry_validity`
in `ideas/execute.py` rejects any idea whose level is already breached or whose stop
is tighter than the timeframe floor, so a careless read can no longer place a
self-destructing order. The box is how a human auditor checks the *judgement* behind
the numbers the machine couldn't.

### 3. Execute + resolve (P3 — `tradingview_ideas.py run`)
```bash
python tradingview_ideas.py run --probe   # preview: route + which fill / invalidate
python tradingview_ideas.py run           # place resting orders + fill/resolve open
```
- **Place (limit/stop order at the author's entry — never market-or-reject):**
  each `extracted` idea rests as a `pending` order at its entry; it is NOT thrown
  away because the live price hasn't reached the entry. Routed by asset:
  - **crypto** → `binance_sim` (resolved on Binance klines)
  - **US equity** → **real Alpaca paper bracket (OCO)** order
- **Invalidated:** if the bracket geometry is impossible (target/stop on the wrong
  side of entry) **OR the pre-placement gate (`_entry_validity`) rejects it** — i.e.
  at the live price the target is already reached, the stop is already breached, or
  (equities) the stop is tighter than the timeframe floor (`MIN_STOP_FRAC`). This is
  the rail that stops a marketable wrong-side entry from stranding the stop on the
  fill (the NOW/GOOGL/AAPL losses). **No venue:** FX/metals/unsupported symbols.
- **Fill + resolve:** crypto walks the real **1m klines** from when we saw the
  idea — first bar to touch the entry fills it, then the first to touch target →
  win / stop → loss (a bar straddling both = loss, pessimistic); past max-hold →
  `flat`. Equities are filled + exited by the **Alpaca broker** (the OCO holds the
  exit) and resolved by polling the order. Long **and** short both work.
- Re-run `tradingview_ideas.py run` each session (or on a cron) — orders fill + brackets resolve over
  the following hours/days as price reaches a level. **No babysitting.**

> **`binance_sim` = honest sim, not a broker fill.** Entry + resolution use REAL
> public Binance prices with deterministic no-lookahead TP/SL, but no order is
> placed (spot testnet is long-only + region-blocks signed orders; shorts need the
> futures testnet from the Frankfurt worker — the next upgrade). Money floor
> untouched.

### 4. Audit — verify results against the broker (automatic, every `run`)
`tradingview_ideas.py run` ends by calling `ideas.execute.audit()`, which
reconciles every open/pending idea against **Bybit/Alpaca** and prints either
`[audit] ✓ N open/pending ideas match the brokers` or a `⚠ DISAGREE` line per
mismatch. **A clean audit is the gate** — our recorded P&L/outcomes must equal the
venue's truth, never our own bookkeeping. If you see a `⚠` line, investigate (a
recorded result drifted from the broker); a clean ✓ means we're honest.

### 5. Verify + ship
```bash
python tradingview_ideas.py show    # confirm the rows look right
```
The board updates live on the dashboard. **Ideas we can't execute show as
"can't execute (no broker API for their market)"** — they are NOT paper-traded
(no sim fallback). Commit + deploy as in `daily_run.md` (`git push origin master`,
then trigger the dashboard + cron Render deploys).

---

## Goal: keep ~50 trades running
The target is a **full book of ~50 concurrent trades**. Each run: scrape a big
batch across ALL the feeds, read EVERY `needs_vision` chart (automatically), set
levels, and `run` to place them — until we're at/near the 50 cap. If the book is
well below 50, scrape more pages and read more charts; the funnel
(`ideas/scrape.py` `_LISTING_PAGES`) covers crypto/stocks/forex/indices/futures +
popular/recent/editors'-picks + extra pages, so there are always more ideas to
pull. Use a high scrape limit (e.g. `scrape --limit 50`).

## Rules / floors (never cross)
- **≤ 50 open trades** — global cap on concurrent LIVE demo positions
  (`status='open'`), checked before any scrape (compute governor).
- **≤ 1 long + 1 short per symbol** (`MAX_PER_SYMBOL_SIDE`, by NORMALISED instrument:
  XAUUSD≡GOLD≡PAXG, BTCUSD≡BTCUSDT≡BTC). The placement gate skips a third trade on a
  symbol/side → `skipped_dup`. Stops the book filling with 8 gold longs / 5 SPCX
  longs, and keeps each Bybit symbol under its **10-conditional-order cap** (the bug
  that left gold positions naked — see docs/TRADE_AUDIT.md).
- **Bracket attached at entry, never naked** — Bybit entries place with TP+SL
  ATTACHED (`place_entry_bracket`, tpslMode=Full, hedge positionIdx); the position is
  protected the instant it fills. No more bare-entry → attach-on-next-cycle window.
- **SL/TP must follow the §2.5 rules, enforced in code:** the placement gate
  (`_entry_validity`) rejects, on BOTH venues, a stop tighter than the timeframe floor
  (`MIN_STOP_FRAC`), an already-played-out level, a >1 R marketable drift, and
  (`MIN_RR`) a reward:risk below **0.8** (a target barely past entry can only lose).
- **Timeframe-agnostic** — trades of ANY timeframe are taken (1m … 1M); the TF only
  sets the max-hold + the stop-distance floor. It never drops an idea.
- **Demo / paper only** — real money still needs `LIVE_BUDGET_ARMED=1` (off).
- **AI/agent-generated levels are tagged** `basis=generated` — never presented as
  the author's call. Chart-read levels are `basis=chart`; in-text are `basis=text`.
- **One independent trade per idea** — no consensus/voting across ideas; the
  broker OCO owns the exit.

> **The book runs itself now.** A Render **cron (`ideas-resolver`, every 30 min)**
> runs `daily.py` → places `extracted` ideas, fills resting orders, resolves open
> positions, and audits vs the brokers. Before this existed the pipeline only moved
> when a human ran it, so it sat stale for days ("no trades executing"). The scrape +
> chart-read still runs here, manually, 1–2×/day.

## Toward automation (when it shows promise)
The manual chart-read **is the validation gate** for whether an automated VLM is
worth paying for. Once a batch of chart-read ideas resolves and shows the read is
reliable + the ideas carry edge:
1. set `ANTHROPIC_API_KEY` (Claude `claude-haiku-4-5` vision, already wired in
   `ideas/scrape.py` `_vision_extract`) or `NVIDIA_API_KEY` for a free NIM VLM,
2. `ideas/scrape.py` then reads charts itself on every run — the manual step in §2
   disappears, and this can move to a Render cron like `daily.py`.

## Commands (one entry-point: `tradingview_ideas.py`)
```
python tradingview_ideas.py scrape --limit N   scrape N new ideas (default 10)
python tradingview_ideas.py vision             ideas awaiting a chart read (JSON)
python tradingview_ideas.py show               print the ideas table
python tradingview_ideas.py set ID --tf 4h --direction -1 \
    --entry E --target T --stop S [--basis chart|generated|text] [--confidence C]
python tradingview_ideas.py run                place orders + fill/resolve (test venues)
python tradingview_ideas.py run --probe        preview routing, no DB writes
python tradingview_ideas.py run --open         only place resting orders
python tradingview_ideas.py run --resolve      only fill pending + resolve open
python tradingview_ideas.py all --limit N      scrape then run, in one go
```

---

## ⏳ Pending work — carried over from the 2026-06-19 run (rebuild toward 50)

**Book state at hand-off:** 29 live (15 pending + 14 open). 6 cap-blocked
(`skipped_dup`). Target is 50, so **~21 more** tradeable ideas need extracting from
the unread scrape backlog.

**62 `needs_vision` ideas remain unread.** This session hit Claude Code's
per-session image-read ceiling (~24 charts) before clearing them. Charts were
downloaded + resized to ≤1800px under `data/charts/idea_<id>.png` (gitignored).
**Next session: resume the chart reads** — `python tradingview_ideas.py vision`
lists them, or pull `chart_image_url` from Neon (`status='needs_vision'`). Read
each, then write levels with **direct Neon SQL** (NOT `set` — that hits local
SQLite unless `DATABASE_URL` is exported), `UPDATE ideas SET direction, timeframe,
entry, stop, target, status='extracted' WHERE id=<id>`. Then `run`.

Highest-signal unread ones already carry a direction/TF from the text pass (read
these first): 110 BTCUSD↑1h, 113 BTCUSD↓1h, 92 BTCUSDT↑4h, 103 BTCUSDT↓4h,
93 BTCUSDT↓2h, 177 WFC↑, 175 INTC↑, 174 RBLX↑, 171 GME↑, 170 AXON↑, 166 AAPL↑,
168 HOOD↑. The rest (dir=0) need direction read off the chart too.

**Extracted this run (8, placed via `run`):** 129 BTCUSD↓, 95 ETHUSD↑, 124 SOLUSDT↓,
136 XAUUSD↑ (cap-dup), 125 LDOUSDT↑, 149 TSLA↑ (cap-dup), 165 NVDA↓, 144 XRPUSD↑.
~24 invalidated (stale entry / no explicit levels / old chart).

**Doc fix needed:** this runbook says an `ideas-resolver` cron runs every 30 min.
That service is in `render.yaml` but **NOT provisioned** on Render. The deployed
cron is **`signal-daily`** (`crn-d8o1svjeo5us738btld0`, every 2h, runs `daily.py`)
— it fills the same role. Either provision `ideas-resolver` or update this note.
