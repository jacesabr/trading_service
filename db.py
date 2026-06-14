"""
db.py — Trade/signal store. Postgres (Neon) in production, SQLite locally.

Selects backend by DATABASE_URL:
  * set (postgresql://...)  -> Neon/Postgres via psycopg2
  * unset                   -> local SQLite file (tracker.db)

Same API either way: init / record_signal / record_bet / resolve_bet /
record_trade / resolve_trade / open_positions / stats.

Neon: create a project, copy the pooled connection string into DATABASE_URL
(append ?sslmode=require). Render injects it as an env var.
"""
import json
import os
import time

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith("postgres")

if IS_PG:
    import psycopg2
    import psycopg2.extras

    def conn():
        c = psycopg2.connect(DATABASE_URL, connect_timeout=10,
                             cursor_factory=psycopg2.extras.RealDictCursor)
        return c
    PH = "%s"           # paramstyle
    SERIAL = "SERIAL PRIMARY KEY"
    RETURN_ID = " RETURNING id"
else:
    import sqlite3
    DB_PATH = os.environ.get("TRADE_DB", "tracker.db")

    def conn():
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c
    PH = "?"
    SERIAL = "INTEGER PRIMARY KEY AUTOINCREMENT"
    RETURN_ID = ""


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS signals(
  id {SERIAL},
  ts BIGINT, strategy TEXT, symbol TEXT, timeframe TEXT,
  direction INTEGER, rule TEXT, mode TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS bets(
  id {SERIAL},
  signal_id INTEGER, ts BIGINT, symbol TEXT, side TEXT,
  best_bid DOUBLE PRECISION, best_ask DOUBLE PRECISION,
  entry_price DOUBLE PRECISION, fee_frac DOUBLE PRECISION,
  window_start BIGINT, outcome TEXT, won INTEGER, pnl DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS trades(
  id {SERIAL},
  signal_id INTEGER, ts BIGINT, symbol TEXT, side TEXT,
  entry DOUBLE PRECISION, stop DOUBLE PRECISION, target DOUBLE PRECISION,
  exit DOUBLE PRECISION, outcome TEXT, won INTEGER,
  ret_bps DOUBLE PRECISION, bars_held INTEGER);
"""
# SQLite needs DOUBLE PRECISION -> REAL, BIGINT ok; it tolerates these but be safe:
if not IS_PG:
    SCHEMA = SCHEMA.replace("DOUBLE PRECISION", "REAL").replace("BIGINT", "INTEGER")

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_sig_ts ON signals(ts)",
    "CREATE INDEX IF NOT EXISTS ix_bet_sig ON bets(signal_id)",
    "CREATE INDEX IF NOT EXISTS ix_trade_sig ON trades(signal_id)",
]


def init():
    c = conn(); cur = c.cursor()
    if IS_PG:
        cur.execute(SCHEMA)
    else:
        cur.executescript(SCHEMA)
    for ix in INDEXES:
        cur.execute(ix)
    c.commit(); cur.close(); c.close()


def _insert(table, cols, vals):
    c = conn(); cur = c.cursor()
    ph = ",".join([PH] * len(vals))
    sql = f"INSERT INTO {table}({','.join(cols)}) VALUES({ph}){RETURN_ID}"
    cur.execute(sql, vals)
    rid = cur.fetchone()["id"] if IS_PG else cur.lastrowid
    c.commit(); cur.close(); c.close()
    return rid


def record_signal(strategy, symbol, timeframe, direction, rule="", mode="paper",
                  detail=None, ts=None):
    return _insert("signals",
                   ["ts", "strategy", "symbol", "timeframe", "direction",
                    "rule", "mode", "detail"],
                   [ts or int(time.time()), strategy, symbol, timeframe,
                    direction, rule, mode, json.dumps(detail or {})])


def record_bet(signal_id, symbol, side, best_bid, best_ask, entry_price,
               fee_frac, window_start, ts=None):
    return _insert("bets",
                   ["signal_id", "ts", "symbol", "side", "best_bid", "best_ask",
                    "entry_price", "fee_frac", "window_start", "outcome",
                    "won", "pnl"],
                   [signal_id, ts or int(time.time()), symbol, side, best_bid,
                    best_ask, entry_price, fee_frac, window_start, "", None, None])


def resolve_bet(bet_id, outcome, won, pnl):
    c = conn(); cur = c.cursor()
    cur.execute(f"UPDATE bets SET outcome={PH},won={PH},pnl={PH} WHERE id={PH}",
                (outcome, int(won), pnl, bet_id))
    c.commit(); cur.close(); c.close()


def record_trade(signal_id, symbol, side, entry, stop, target, ts=None):
    return _insert("trades",
                   ["signal_id", "ts", "symbol", "side", "entry", "stop",
                    "target", "exit", "outcome", "won", "ret_bps", "bars_held"],
                   [signal_id, ts or int(time.time()), symbol, side, entry,
                    stop, target, None, "", None, None, None])


def resolve_trade(trade_id, exit_price, outcome, won, ret_bps, bars_held):
    c = conn(); cur = c.cursor()
    cur.execute(f"UPDATE trades SET exit={PH},outcome={PH},won={PH},ret_bps={PH},"
                f"bars_held={PH} WHERE id={PH}",
                (exit_price, outcome, int(won), ret_bps, bars_held, trade_id))
    c.commit(); cur.close(); c.close()


def _rows(sql, args=()):
    c = conn(); cur = c.cursor()
    cur.execute(sql, args)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); c.close()
    return rows


def open_positions():
    bets = _rows("SELECT * FROM bets WHERE outcome=''")
    trades = _rows("SELECT * FROM trades WHERE outcome=''")
    return bets, trades


def stats(strategy=None):
    bw = f" AND s.strategy={PH}" if strategy else ""
    args = (strategy,) if strategy else ()
    bets = _rows("SELECT b.*, s.strategy FROM bets b JOIN signals s "
                 f"ON b.signal_id=s.id WHERE b.outcome!=''{bw}", args)
    trades = _rows("SELECT t.*, s.strategy FROM trades t JOIN signals s "
                   f"ON t.signal_id=s.id WHERE t.outcome!=''{bw}", args)
    return bets, trades


if __name__ == "__main__":
    init()
    print(f"initialized ({'Postgres/Neon' if IS_PG else 'SQLite'})")
    sid = record_signal("meanrev", "BTCUSDT", "5m", -1, "overbought_strong")
    bid = record_bet(sid, "BTCUSDT", "Down", 0.51, 0.52, 0.52, 0.018,
                     int(time.time()) // 300 * 300)
    resolve_bet(bid, "Down", 1, 100/0.52 - 100 - 100*0.018)
    b, t = stats()
    print(f"self-test: {len(b)} bets logged, sample pnl {b[-1]['pnl']:.2f}")
