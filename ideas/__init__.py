"""ideas — TradingView Ideas pipeline (scrape → chart-read → demo-execute).

Implementation modules (driven by the single root entry-point
`tradingview_ideas.py`, not run directly):
  ideas.scrape   — pull community ideas off TradingView (Tavily), store in the
                   `ideas` table, manual chart-read writeback (set-levels).
  ideas.execute  — route symbol → venue, place limit/stop orders at the author's
                   entry, fill + resolve the bracket against real Binance klines
                   (venue binance_sim). Long + short.

Paper/demo only — the money floor (LIVE_BUDGET_ARMED) is never crossed here.
"""
