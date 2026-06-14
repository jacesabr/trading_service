# Setup — Polymarket 5m Paper-Trading System + Dashboard

## 0. Where to run it
Any always-on machine that can reach **api.binance.com**. Your machine in
India works. If you use a VPS, pick **Singapore/EU** region — US-hosted IPs
get HTTP 451 from Binance. 1 vCPU / 1GB is plenty (~$5/mo: Hetzner, Vultr,
DigitalOcean).

## 1. Install (5 minutes)
```bash
mkdir ~/pm5m && cd ~/pm5m
# copy in: paper_trader.py  dashboard.py  rules.json
# plus their imports: indicator_battery.py  confluence_search.py  data.py
python3 -m venv venv && source venv/bin/activate
pip install pandas numpy flask scikit-learn
# one-time historical data for indicator warmup checks / monthly re-validation
python3 data.py BTCUSDT 1h 5m 2025-06 2026-06
python3 data.py ETHUSDT 1h 5m 2025-06 2026-06
```

## 2. Verify connectivity (run once)
```bash
python3 paper_trader.py --probe
```
Expected: `features ok` per coin, a found market slug, and live best bid/ask.
If gamma-api fails the code falls back to scraping the event page (tested
working). If Binance fails, your network/region is the issue.

## 3. Run 24/7 with pm2 (you already know it from Node)
```bash
pm2 start paper_trader.py --name pm5m-trader --interpreter ./venv/bin/python3
pm2 start dashboard.py    --name pm5m-dash   --interpreter ./venv/bin/python3
pm2 save && pm2 startup    # survive reboots
pm2 logs pm5m-trader       # watch signals live
```
(systemd works too if you prefer; pm2 is the fastest path.)

## 4. Dashboard
- Local: http://localhost:8050
- From your phone, don't expose the port publicly. Install **Tailscale** on
  the server and your phone (free), then open http://<tailscale-ip>:8050.
  Zero config, encrypted, no auth code needed in the app itself.

What it shows (auto-refreshes every 30s):
- **Status strip**: liveness dot (red if no signal for 2h), pending windows,
  signals/day.
- **KPI cards**: resolved count, hit rate vs avg entry price (the kill
  criterion — hit rate must stay ABOVE avg entry), EV/bet taker and maker.
- **Calibration panel** (the verdict chart): entry price vs realized win
  rate per 2¢ bucket. Points above the dashed fair-value line = the market
  underprices your signal. Points on the line = no edge, regardless of win rate.
- **Equity curves**: cumulative P&L under taker (after dynamic fee) and
  maker execution assumptions.
- **Per-rule table**: live win rate vs backtest expectation; flags decay.

## 5. Maintenance
- `python3 paper_trader.py --report` — same stats in the terminal.
- Daily: `cp paper_trades.csv backups/paper_trades_$(date +%F).csv` (cron it).
- Monthly: re-pull data (`data.py ... 2026-06 2026-07`), re-run
  `indicator_battery.py`; retire rules whose validation z drops below 1.5.
- Monthly: re-check the Polymarket fee docs (they've changed twice in 2026):
  docs.polymarket.com/developers/market-makers/maker-rebates-program
- Decision gates after ~400 resolved signals: see PLAN.md §4.

## 6. File map
| file | role |
|---|---|
| paper_trader.py | 24/7 signal + paper-execution + resolution logger |
| rules.json | the validated rules (edit to add/disable rules, no code change) |
| dashboard.py | Flask monitor on :8050 |
| paper_trades.csv | the dataset everything is judged on (back it up) |
| indicator_battery.py | monthly re-validation of rules on fresh data |
| PLAN.md | research summary, decision gates, service path |

---
# Add-on: LLM analysis + execution layer (added later)

## 7. API keys (environment)
```bash
export ANTHROPIC_API_KEY=sk-ant-...   # daily deep analysis (Claude / Fable)
export NVIDIA_API_KEY=nvapi-...       # live assist in the dashboard (NIM)
export NIM_MODEL=meta/llama-3.3-70b-instruct   # optional override
export ASSIST_PROVIDER=nvidia         # which provider the Ask box uses
```
Note: the Anthropic API is billed separately from your claude.ai Max plan —
one Fable report/day on summarized stats costs cents. Keys go in
`~/.bashrc` or a `.env` you source before pm2 start.

## 8. Daily analyst (Fable) run
```bash
python3 analyst.py --dry        # preview the exact prompt, calls nothing
python3 analyst.py              # one real run -> reports/YYYY-MM-DD.md
pm2 start analyst.py --name pm5m-analyst --interpreter ./venv/bin/python3 \
    --cron "30 0 * * *" --no-autorestart        # daily 00:30
```
The analyst receives computed statistics only (never raw rows — it cannot
hallucinate your data), writes the report the dashboard displays, and
appends proposed experiments to SUGGESTIONS.md. **It never edits
rules.json** — you approve experiments by hand. Keep it that way.

## 9. Live assist (NIM)
The dashboard's "Ask" box posts your question plus the current verified
stats to NIM. Good for "is rule X decaying?" mid-day without burning the
daily report. NIM free-tier rate limits are fine for on-demand use; don't
wire it into the per-signal hot path.

## 10. Execution layer (paper -> live)
`executor.py` exposes one interface for both modes:
- **paper** (default): simulated fills from live book snapshots — this IS
  the paper-trading API; Polymarket offers no official one.
- **live**: real maker-only limit orders via the official CLOB
  (`pip install py-clob-client`). One-time setup: dedicated Polygon wallet
  holding ONLY the bankroll, funded with USDC.e, one manual UI trade to set
  allowances, then `export POLY_PRIVATE_KEY=0x...`.
  Hard rails in the file: $20 max/order, $60 daily-loss kill switch,
  maker-only pricing, and it refuses to construct without
  `confirm_live=True`. Do not raise the caps until PLAN.md §4 promotion
  criteria are met on 400+ paper signals.
