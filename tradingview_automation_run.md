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
  in the **`ideas`** table. Enforces the **20 open-trade cap** (skips scraping at
  cap). **Timeframe-agnostic** — every TF is accepted.
- **dashboard** — `/api/ideas` + a **TradingView Ideas** board at the top of the
  main page (chart thumbnail, symbol, dir, entry/target/stop, TF, basis, conf,
  status, outcome, author, boosts, link).
- **`ideas/execute.py`** — P3 demo execution. Routes each `extracted` idea's symbol
  to a Binance USDT pair, market-enters at the live price, and resolves the
  bracket against real 1m klines (no-lookahead). Venue `binance_sim` (real prices,
  honest sim — not a broker fill). Long + short both work.
- **status lifecycle:** `needs_vision` → (chart read) → `extracted`
  → (`run`) → `pending` → `open` → `resolved` (target/stop/flat). Side paths:
  `invalidated` (market moved past the level), `no_venue` (unsupported symbol).
- **Not built yet (P4+):** engagement analytics (does boosts/author predict
  win-rate). Real broker fills for shorts (futures testnet from Frankfurt) is the
  execution upgrade beyond `binance_sim`.

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

### 2. Read the charts (Claude Code vision — the manual keystone)
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
- **Open:** each `extracted` idea is routed (symbol → Binance USDT pair) and
  **market-entered at the live Binance price now** (the realtime intent); the
  idea's target/stop become the bracket. status `extracted → open`.
- **Invalidated:** if the market has already moved past the author's entry/stop
  (the drawn setup can no longer be entered), it's marked `invalidated` — not
  forced in at a bad price.
- **No venue:** non-crypto / unsupported symbols → `no_venue` (tracked, not traded).
- **Resolve:** walks the real **1m klines** since entry; first bar to touch the
  target → `target` (win), the stop → `stop` (loss); a bar straddling both is
  scored a **loss** (pessimistic, never inflate). Past the TF's max-hold with no
  touch → `flat` at the last close. Long **and** short both resolve correctly.
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

## Rules / floors (never cross)
- **≤ 20 open trades** — global cap on concurrent LIVE demo positions
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
