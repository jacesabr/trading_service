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
CREATE TABLE IF NOT EXISTS executions(
  id {SERIAL},
  signal_id INTEGER, ts BIGINT, venue TEXT, symbol TEXT, side TEXT,
  entry DOUBLE PRECISION, stop DOUBLE PRECISION, target DOUBLE PRECISION,
  exit DOUBLE PRECISION, outcome TEXT, won INTEGER,
  ret_bps DOUBLE PRECISION, bars_held INTEGER, ref TEXT);
CREATE TABLE IF NOT EXISTS experiments(
  id {SERIAL},
  ts BIGINT, strategy TEXT, kind TEXT, hypothesis TEXT,
  manifest TEXT, config TEXT, result TEXT,
  verdict TEXT, robust TEXT, by_who TEXT);
CREATE TABLE IF NOT EXISTS strategy_versions(
  id {SERIAL},
  ts BIGINT, strategy TEXT, before_params TEXT, after_params TEXT,
  reason TEXT, revalidation TEXT, by_who TEXT);
CREATE TABLE IF NOT EXISTS lessons(
  id {SERIAL},
  ts BIGINT, idea TEXT, domain TEXT, verdict TEXT, evidence TEXT,
  redo_bar TEXT, by_who TEXT);
"""
# SQLite needs DOUBLE PRECISION -> REAL, BIGINT ok; it tolerates these but be safe:
if not IS_PG:
    SCHEMA = SCHEMA.replace("DOUBLE PRECISION", "REAL").replace("BIGINT", "INTEGER")

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_sig_ts ON signals(ts)",
    "CREATE INDEX IF NOT EXISTS ix_bet_sig ON bets(signal_id)",
    "CREATE INDEX IF NOT EXISTS ix_trade_sig ON trades(signal_id)",
    "CREATE INDEX IF NOT EXISTS ix_exec_sig ON executions(signal_id)",
    "CREATE INDEX IF NOT EXISTS ix_exp_strat ON experiments(strategy)",
    "CREATE INDEX IF NOT EXISTS ix_exp_ts ON experiments(ts)",
    "CREATE INDEX IF NOT EXISTS ix_ver_strat ON strategy_versions(strategy)",
    "CREATE INDEX IF NOT EXISTS ix_lesson_ts ON lessons(ts)",
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


def record_signals_trades(items):
    """Bulk insert (signal + trade) pairs. On Postgres uses execute_values so the
    whole batch is ~2 round-trips (essential cross-region against Neon); on SQLite
    a simple loop. items: dicts with strategy,symbol,timeframe,direction,rule,
    detail,side,entry,stop,target,ts. Returns count inserted."""
    if not items:
        return 0
    now = int(time.time())
    sig_rows = [(it.get("ts") or now, it["strategy"], it["symbol"],
                 it.get("timeframe", "5m"), it["direction"], it.get("rule", ""),
                 it.get("mode", "paper"), json.dumps(it.get("detail") or {}))
                for it in items]
    c = conn(); cur = c.cursor()
    if IS_PG:
        ids = psycopg2.extras.execute_values(
            cur, "INSERT INTO signals(ts,strategy,symbol,timeframe,direction,"
                 "rule,mode,detail) VALUES %s RETURNING id", sig_rows, fetch=True)
        sids = [r["id"] if isinstance(r, dict) else r[0] for r in ids]
        tr_rows = [(sids[i], sig_rows[i][0], it["symbol"], it["side"],
                    it["entry"], it.get("stop"), it.get("target"), None, "",
                    None, None, None) for i, it in enumerate(items)]
        psycopg2.extras.execute_values(
            cur, "INSERT INTO trades(signal_id,ts,symbol,side,entry,stop,target,"
                 "exit,outcome,won,ret_bps,bars_held) VALUES %s", tr_rows)
    else:
        for i, it in enumerate(items):
            cur.execute(f"INSERT INTO signals(ts,strategy,symbol,timeframe,"
                        f"direction,rule,mode,detail) VALUES({','.join([PH]*8)})",
                        sig_rows[i])
            sid = cur.lastrowid
            cur.execute(f"INSERT INTO trades(signal_id,ts,symbol,side,entry,stop,"
                        f"target,exit,outcome,won,ret_bps,bars_held) "
                        f"VALUES({','.join([PH]*12)})",
                        (sid, sig_rows[i][0], it["symbol"], it["side"],
                         it["entry"], it.get("stop"), it.get("target"), None,
                         "", None, None, None))
    c.commit(); cur.close(); c.close()
    return len(items)


def resolve_trades_batch(updates):
    """Bulk-resolve trades over ONE connection.
    updates: (exit_price, outcome, won, ret_bps, bars_held, trade_id) tuples."""
    if not updates:
        return 0
    c = conn(); cur = c.cursor()
    cur.executemany(
        f"UPDATE trades SET exit={PH},outcome={PH},won={PH},ret_bps={PH},"
        f"bars_held={PH} WHERE id={PH}",
        [(e, o, int(w), r, b, i) for (e, o, w, r, b, i) in updates])
    c.commit(); cur.close(); c.close()
    return len(updates)


def resolve_trade(trade_id, exit_price, outcome, won, ret_bps, bars_held):
    c = conn(); cur = c.cursor()
    cur.execute(f"UPDATE trades SET exit={PH},outcome={PH},won={PH},ret_bps={PH},"
                f"bars_held={PH} WHERE id={PH}",
                (exit_price, outcome, int(won), ret_bps, bars_held, trade_id))
    c.commit(); cur.close(); c.close()


def record_execution(signal_id, venue, symbol, side, entry, stop, target,
                     ref="", ts=None):
    """A REAL paper fill on an external venue (OANDA/Alpaca/testnet/Kalshi),
    recorded alongside the internal-sim trade for the same signal."""
    return _insert("executions",
                   ["signal_id", "ts", "venue", "symbol", "side", "entry",
                    "stop", "target", "exit", "outcome", "won", "ret_bps",
                    "bars_held", "ref"],
                   [signal_id, ts or int(time.time()), venue, symbol, side,
                    entry, stop, target, None, "", None, None, None, ref])


def resolve_execution(exec_id, exit_price, outcome, won, ret_bps, bars_held):
    c = conn(); cur = c.cursor()
    cur.execute(f"UPDATE executions SET exit={PH},outcome={PH},won={PH},"
                f"ret_bps={PH},bars_held={PH} WHERE id={PH}",
                (exit_price, outcome, int(won), ret_bps, bars_held, exec_id))
    c.commit(); cur.close(); c.close()


def open_executions():
    return _rows("SELECT * FROM executions WHERE outcome=''")


def recent_executions(limit=2000):
    return _rows("SELECT e.*, s.strategy FROM executions e JOIN signals s "
                 "ON e.signal_id=s.id WHERE e.outcome!='' "
                 f"ORDER BY e.ts DESC LIMIT {int(limit)}")


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


def activity():
    """Per-strategy operational state: last signal time, total + pending count.
    Returns {strategy: {last_ts, n_sig, pending}}."""
    out = {}
    for r in _rows("SELECT strategy, MAX(ts) AS last_ts, COUNT(*) AS n "
                   "FROM signals GROUP BY strategy"):
        out[r["strategy"]] = dict(last_ts=int(r["last_ts"] or 0),
                                  n_sig=int(r["n"]), pending=0)
    for tbl in ("bets", "trades"):
        for r in _rows(f"SELECT s.strategy AS strategy, COUNT(*) AS c "
                       f"FROM {tbl} x JOIN signals s ON x.signal_id=s.id "
                       f"WHERE x.outcome='' GROUP BY s.strategy"):
            out.setdefault(r["strategy"],
                           dict(last_ts=0, n_sig=0, pending=0))
            out[r["strategy"]]["pending"] += int(r["c"])
    return out


def recent_trades(limit=400):
    """Resolved trades joined with strategy + signal detail (carries the live
    spread_bps), newest first (for per-trade logs and real-cost netting)."""
    return _rows("SELECT t.*, s.strategy, s.detail FROM trades t JOIN signals s "
                 "ON t.signal_id=s.id WHERE t.outcome!='' "
                 f"ORDER BY t.ts DESC LIMIT {int(limit)}")


def recent_trades_capped(per=2000):
    """Resolved trades, newest `per` PER STRATEGY (not a single global LIMIT).

    A global LIMIT silently crowded older / low-frequency strategies out of the
    window, so the dashboard rendered them as 0-trade even though they had
    hundreds of resolved trades. Partitioning per strategy fixes that while
    staying bounded (~n_strategies * per rows)."""
    if IS_PG:
        return _rows(
            "SELECT * FROM (SELECT t.*, s.strategy, s.detail, "
            "ROW_NUMBER() OVER (PARTITION BY s.strategy ORDER BY t.ts DESC) AS rn "
            "FROM trades t JOIN signals s ON t.signal_id=s.id "
            f"WHERE t.outcome!='') q WHERE rn <= {int(per)}")
    # SQLite fallback: a generous global window (local dev has far fewer rows).
    return _rows("SELECT t.*, s.strategy, s.detail FROM trades t JOIN signals s "
                 "ON t.signal_id=s.id WHERE t.outcome!='' "
                 f"ORDER BY t.ts DESC LIMIT {int(per) * 30}")


def recent_bets(limit=400):
    return _rows("SELECT b.*, s.strategy FROM bets b JOIN signals s "
                 "ON b.signal_id=s.id WHERE b.outcome!='' "
                 f"ORDER BY b.ts DESC LIMIT {int(limit)}")


# ---------------- R&D ledger (experiments / versions / lessons) -------------
def _dump(x):
    return x if isinstance(x, str) or x is None else json.dumps(x)


def record_experiment(strategy, kind, hypothesis="", manifest=None, config=None,
                      result=None, verdict="", robust=None, by_who="agent",
                      ts=None):
    """One research/backtest run. kind: leaktest|walkforward|gridsearch|backtest.
    result/manifest/config/robust may be dicts (JSON-encoded here)."""
    return _insert("experiments",
                   ["ts", "strategy", "kind", "hypothesis", "manifest", "config",
                    "result", "verdict", "robust", "by_who"],
                   [ts or int(time.time()), strategy, kind, hypothesis,
                    _dump(manifest), _dump(config), _dump(result), verdict,
                    _dump(robust), by_who])


def record_version(strategy, before_params, after_params, reason="",
                   revalidation=None, by_who="agent", ts=None):
    """A param tweak with its before/after and the re-validation result that
    must pass before it can affect money."""
    return _insert("strategy_versions",
                   ["ts", "strategy", "before_params", "after_params", "reason",
                    "revalidation", "by_who"],
                   [ts or int(time.time()), strategy, _dump(before_params),
                    _dump(after_params), reason, _dump(revalidation), by_who])


def record_lesson(idea, domain="", verdict="rejected", evidence="", redo_bar="",
                  by_who="agent", ts=None):
    """The system's do-not-repeat memory: why an idea was rejected and the bar
    that would justify revisiting it. Read first every research run."""
    return _insert("lessons",
                   ["ts", "idea", "domain", "verdict", "evidence", "redo_bar",
                    "by_who"],
                   [ts or int(time.time()), idea, domain, verdict, evidence,
                    redo_bar, by_who])


def experiments(strategy=None, limit=400):
    w = f" WHERE strategy={PH}" if strategy else ""
    args = (strategy,) if strategy else ()
    return _rows(f"SELECT * FROM experiments{w} ORDER BY ts DESC "
                 f"LIMIT {int(limit)}", args)


def versions(strategy=None, limit=400):
    w = f" WHERE strategy={PH}" if strategy else ""
    args = (strategy,) if strategy else ()
    return _rows(f"SELECT * FROM strategy_versions{w} ORDER BY ts DESC "
                 f"LIMIT {int(limit)}", args)


def lessons(limit=1000):
    return _rows(f"SELECT * FROM lessons ORDER BY ts DESC LIMIT {int(limit)}")


if __name__ == "__main__":
    init()
    print(f"initialized ({'Postgres/Neon' if IS_PG else 'SQLite'})")
    sid = record_signal("meanrev", "BTCUSDT", "5m", -1, "overbought_strong")
    bid = record_bet(sid, "BTCUSDT", "Down", 0.51, 0.52, 0.52, 0.018,
                     int(time.time()) // 300 * 300)
    resolve_bet(bid, "Down", 1, 100/0.52 - 100 - 100*0.018)
    b, t = stats()
    print(f"self-test: {len(b)} bets logged, sample pnl {b[-1]['pnl']:.2f}")
