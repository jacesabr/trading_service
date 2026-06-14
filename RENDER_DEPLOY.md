# Deploy on Render + Neon — step by step

## Cost: ~$7/mo (worker) + $0 (free dashboard) + $0 (Neon free tier)

## Step 1 — Neon database (5 min, free)
1. neon.tech -> sign up -> New Project. Region: pick **AWS eu-central-1
   (Frankfurt)** to match the Render region below.
2. Dashboard -> Connection Details -> toggle **Pooled connection** -> copy the
   string. Looks like:
   postgresql://USER:PASS@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require
   This is your DATABASE_URL. (Pooled, not direct — multiple services connect.)

## Step 2 — GitHub
Push this folder to a new GitHub repo. The .gitignore already excludes
*.db, *.csv, .env so no data or secrets get committed. Verify with:
  git status   (should NOT list tracker.db or any *.csv)

## Step 3 — Render
1. render.com -> New -> Blueprint -> connect the repo. It reads render.yaml and
   shows two services: signal-dashboard (free) + signal-runner (starter, paid).
2. Click Apply. Render will ask you to confirm the paid worker — accept.
3. In EACH service -> Environment, add:
   - signal-runner:   DATABASE_URL, NVIDIA_API_KEY, ANTHROPIC_API_KEY
   - signal-dashboard: DATABASE_URL
   (Render won't auto-fill sync:false vars — paste them manually.)
4. First deploy runs db.init() automatically (web on boot, worker on start).

## Step 4 — Verify (the part that catches people)
- Open the worker (signal-runner) -> Logs. Within ~5 min you should see a cycle
  line. If you see "binance error" / HTTP 451: the region didn't take — confirm
  it's Frankfurt, not a US region. Redeploy if needed.
- Open the dashboard URL Render gives you. Panels show "No bets yet" until the
  first 5m window with a signal resolves (can take a while — meanrev only fires
  on overbought/oversold; gaptrav fires on gap-closes, more often).
- Neon -> SQL Editor -> `select strategy, count(*) from signals group by 1;`
  to confirm rows are landing.

## Step 5 — Phone access (free, optional)
The dashboard is a public Render URL — just open it on your phone. (Free web
services sleep after 15 min idle and take ~30s to wake; the data is safe in
Neon regardless. If you want it always-warm, that's the $7 web tier, but for a
personal monitor the free sleep is fine.)

## What's running
- meanrev: RSI/MFI 5m -> Polymarket binary. Paper mode (logs real book fills,
  no capital). The live candidate.
- gaptrav: gap-traversal forex. Paper experiment, watched for divergence.
Both write to Neon; the dashboard reads it. Nothing trades real money until you
flip executor.py to live mode with a funded wallet — which you should NOT do
until the meanrev calibration curve sits above the diagonal (see PLAN.md gates).

## Cost control
- Only the worker is paid. If you want to pause spending, suspend the
  signal-runner service in Render — the dashboard and Neon data stay live, you
  just stop collecting new signals. Resume anytime.
- Neon free tier is generous for this (tiny row volume). You won't hit limits.
