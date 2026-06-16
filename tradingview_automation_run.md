# tradingview_automation_run.md — the TradingView Ideas run (1–2× daily, via Claude Code)

Run this **once or twice a day by hand in Claude Code**. It scrapes community
trading ideas off TradingView, reads each idea's chart with **Claude Code's own
vision** (no API key yet — *you*, the agent in this session, are the vision
model), records a structured trade, and shows everything on the dashboard's
**TradingView Ideas** board.

Companion to `daily_run.md` (the strategy-lab research run). That one researches
signals; this one turns the crowd's published ideas into a scored, demo-executed
track record. **Paper / demo only** — the live path stays gated off.

> **No API keys yet — by design.** The vision step is done manually by Claude
> Code reading the chart images. The code is already wired for an automated VLM:
> set `ANTHROPIC_API_KEY` (or `NVIDIA_API_KEY`) and `ideas/scrape.py` will read
> charts itself, no manual loop. We stay manual until the idea proves it has edge.

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
  - **crypto** → `binance_sim`: a limit/stop order at the author's entry, filled +
    resolved on real Binance 1m klines (no-lookahead, long+short). Honest sim —
    real prices, not a broker fill.
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
- **Invalidated:** only if the bracket geometry is impossible (target/stop on the
  wrong side of entry). **No venue:** FX/metals/unsupported symbols.
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

### 4. Verify + ship
```bash
python tradingview_ideas.py show    # confirm the rows look right
```
The board updates live on the dashboard (open trades show `live @ <fill>`,
resolved show outcome + bps, with a win/PnL summary line). Commit + deploy as in
`daily_run.md` (`git push origin master`, then trigger the dashboard + cron
Render deploys).

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
- **Timeframe-agnostic** — trades of ANY timeframe are taken; the TF only sets the
  max-hold downstream (a 1d idea holds days, a 5m idea minutes-to-hours). It never
  drops an idea. (Reinstate a cap by setting `MAX_TF_MIN` in `ideas/scrape.py`.)
- **Demo / paper only** — real money still needs `LIVE_BUDGET_ARMED=1` (off).
- **AI/agent-generated levels are tagged** `basis=generated` — never presented as
  the author's call. Chart-read levels are `basis=chart`; in-text are `basis=text`.
- **One independent trade per idea** — no consensus/voting across ideas; the
  broker OCO (when P3 lands) owns the exit.

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
