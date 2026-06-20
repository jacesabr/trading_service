# trading_service

A short-horizon strategy **falsification lab** + live broker-paper tracker.
Everything is paper; live trading is gated off by design.

**Single source of truth → [daily_run.md](daily_run.md)** (browser twin
[daily_run.html](daily_run.html), also served live at `/docs`). The thesis, every
rule, the architecture, the current roster, the research procedure, the
infra/deploy reference, and the do-not-relitigate lessons live there — and only
there. If it isn't in daily_run, it doesn't exist.

```bash
pip install -r requirements.txt
python db.py          # init the store (SQLite, or Neon if DATABASE_URL is set)
python lab.py list    # the strategy roster
python lab.py lessons # do-not-repeat memory (read first)
```
