"""
paper_trader.py — Live paper-trading harness for Polymarket 5-minute
BTC/ETH Up-or-Down markets.

What it does, every 5-minute boundary:
  1. Pulls the just-closed Binance 5m candle history (REST, no key needed).
  2. Computes the validated indicator features and evaluates rules.json.
  3. If a rule fires, finds the Polymarket market for the window that just
     opened (slug pattern: {coin}-updown-5m-{epoch}) and snapshots the live
     order book for the predicted side.
  4. Records a paper fill at the best ask (with depth-aware slippage) and a
     hypothetical maker fill at the bid+1 tick, plus the dynamic taker fee.
  5. After the window closes, resolves the outcome from the Binance candle
     (close >= open -> Up, matching Polymarket's rules) and logs P&L.

Everything is appended to paper_trades.csv + a daily summary on screen.
Run on a machine that can reach api.binance.com (works from India; the
analysis container that built this was geo-blocked, so live operation was
not fully end-to-end tested here — run --probe first).

Usage:
  python3 paper_trader.py                 # run the live loop
  python3 paper_trader.py --probe        # one-shot connectivity/market test
  python3 paper_trader.py --report       # stats from the log so far
"""
import csv
import json
import re
import sys
import time
import datetime as dt
import urllib.request
import numpy as np
import pandas as pd

COINS = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
SIZE_USD = 100.0          # paper stake per signal
FEE_PEAK = 0.0315         # taker fee at p=0.50; declines linearly to 0 at 0/1
LOG = "paper_trades.csv"
RULES_FILE = "rules.json"
FIELDS = ["ts", "coin", "window_start", "rule", "side", "best_bid", "best_ask",
          "ask_depth", "taker_fill", "maker_fill", "fee_frac", "outcome",
          "won", "pnl_taker", "pnl_maker_if_filled", "llm_vote", "llm_note"]
LLM_SHADOW = bool(int(__import__("os").environ.get("LLM_SHADOW", "0")))


def http(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def get_json(url, timeout=15):
    return json.loads(http(url, timeout))


# ---------------- Binance ------------------------------------------------
def klines(symbol, limit=300):
    raw = get_json(f"https://api.binance.com/api/v3/klines"
                   f"?symbol={symbol}&interval=5m&limit={limit}")
    df = pd.DataFrame([r[:6] for r in raw],
                      columns=["ts", "open", "high", "low", "close", "volume"])
    for k in df.columns[1:]:
        df[k] = df[k].astype(float)
    df["ts"] = df["ts"].astype(np.int64)
    return df


def candle_outcome(symbol, window_start_ms):
    raw = get_json(f"https://api.binance.com/api/v3/klines?symbol={symbol}"
                   f"&interval=5m&startTime={window_start_ms}&limit=1")
    o, c = float(raw[0][1]), float(raw[0][4])
    return "Up" if c >= o else "Down"          # ties resolve Up per rules


# ---------------- Polymarket ---------------------------------------------
def find_market(coin, window_epoch):
    """Return dict(token_id_by_outcome) for the 5m market starting at epoch.
    Tries gamma API, falls back to scraping the event page."""
    slug = f"{coin}-updown-5m-{window_epoch}"
    try:
        d = get_json(f"https://gamma-api.polymarket.com/markets?slug={slug}")
        if d:
            m = d[0]
            toks = json.loads(m["clobTokenIds"])
            outs = json.loads(m["outcomes"])
            return dict(zip(outs, toks)), slug
    except Exception:
        pass
    try:                                       # HTML fallback (verified to work)
        html = http(f"https://polymarket.com/event/{slug}", 20).decode(
            "utf-8", "ignore")
        toks = re.search(r'"clobTokenIds":"(\[.*?\])"', html)
        outs = re.search(r'"outcomes":"(\[.*?\])"', html)
        if toks and outs:
            t = json.loads(toks.group(1).replace('\\"', '"'))
            o = json.loads(outs.group(1).replace('\\"', '"'))
            return dict(zip(o, t)), slug
    except Exception:
        pass
    return None, slug


def best_book(token_id):
    b = get_json(f"https://clob.polymarket.com/book?token_id={token_id}")
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])),
                  reverse=True)
    asks = sorted(((float(x["price"]), float(x["size"])) for x in b.get("asks", [])))
    return bids, asks


def taker_fill(asks, usd):
    """Walk the ask side; return avg fill price and depth at best ask."""
    if not asks:
        return None, 0
    remaining, cost, shares = usd, 0.0, 0.0
    for px, sz in asks:
        take = min(sz, remaining / px)
        cost += take * px; shares += take; remaining -= take * px
        if remaining <= 0.01:
            break
    return (cost / shares if shares else None), asks[0][1]


def fee_fraction(p):
    return FEE_PEAK * max(0.0, 1 - abs(2 * p - 1))


# ---------------- signals -------------------------------------------------
def load_rules():
    with open(RULES_FILE) as f:
        return json.load(f)


def compute_features(df):
    from indicator_battery import features
    # lag=False: live signals use the indicator AT the just-closed candle,
    # exactly matching what the backtest's lagged features represent.
    return features(df, lag=False)


def evaluate(rules, feats):
    """Returns (side, rule_name) or (None, None). Last row = signal bar."""
    for r in rules:
        ok = True
        for cond in r["conditions"]:
            x = feats[cond["feature"]][-1]
            if np.isnan(x):
                ok = False; break
            if cond["op"] == ">" and not x > cond["thr"]: ok = False; break
            if cond["op"] == "<" and not x < cond["thr"]: ok = False; break
        if ok:
            return ("Up" if r["dir"] > 0 else "Down"), r["name"]
    return None, None


def shadow_vote(coin, side, rule, feats, bids, asks):
    """Shadow-mode LLM judgment. Logged for later comparison, never gates
    the trade. Latency doesn't matter: the paper fill snapshot is already
    taken before this runs."""
    if not LLM_SHADOW:
        return "", ""
    try:
        from llm_client import ask
        last = {k: round(float(v[-1]), 2) for k, v in feats.items()
                if v[-1] == v[-1]}
        prompt = (f"5-minute {coin.upper()} window just opened. A mean-reversion "
                  f"rule '{rule}' fired, predicting {side}. Indicators at the "
                  f"just-closed candle: {last}. Order book for {side}: "
                  f"best_bid={bids[0] if bids else None}, "
                  f"best_ask={asks[0] if asks else None}. "
                  f"Vote whether taking this signal is +EV at the ask. "
                  f"Reply EXACTLY: TRADE|<10 words> or SKIP|<10 words>")
        r = ask(prompt, provider="nvidia", max_tokens=60,
                system="You are a strict quantitative trade reviewer. "
                       "One line only, format: TRADE|reason or SKIP|reason")
        vote, _, note = r.strip().partition("|")
        return vote.strip().upper()[:5], note.strip()[:80]
    except Exception as e:
        return "ERR", str(e)[:60]


# ---------------- logging / report ----------------------------------------
def append_row(row):
    new = not pd.io.common.file_exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def report():
    df = pd.read_csv(LOG)
    df = df[df.outcome.notna()]
    if df.empty:
        print("no resolved paper trades yet"); return
    print(f"resolved paper trades: {len(df)}")
    for coin, g in df.groupby("coin"):
        hr = g.won.mean()
        print(f"\n{coin}: n={len(g)} hit={hr:.1%} "
              f"avg taker entry={g.taker_fill.mean():.3f} "
              f"EV/bet taker={g.pnl_taker.mean()/SIZE_USD:+.2%} "
              f"EV/bet maker-if-filled={g.pnl_maker_if_filled.mean()/SIZE_USD:+.2%}")
        g = g.copy(); g["bucket"] = (g.taker_fill * 20).round() / 20
        cal = g.groupby("bucket").agg(n=("won", "size"), win=("won", "mean"))
        print(cal.to_string())
    if "llm_vote" in df.columns and df.llm_vote.isin(["TRADE","SKIP"]).any():
        print("\nLLM shadow-vote comparison (the test of 'LLM decides better'):")
        for v, g in df[df.llm_vote.isin(["TRADE","SKIP"])].groupby("llm_vote"):
            print(f"  {v}: n={len(g)} hit={g.won.mean():.1%} "
                  f"EV taker={g.pnl_taker.mean()/SIZE_USD:+.2%}")
        print("If TRADE-EV > SKIP-EV by a significant margin after 200+ votes,"
              "\nthe LLM gate earns promotion. Otherwise the rules stand alone.")
    print("\nCalibration question: in each entry-price bucket, is win% > price?"
          "\nIf yes consistently -> the market is underpricing your signal.")


# ---------------- main loop ------------------------------------------------
def run_once(rules, now_epoch, probe=False):
    boundary = (now_epoch // 300) * 300          # window that just OPENED
    rows = []
    for coin, symbol in COINS.items():
        try:
            df = klines(symbol)
        except Exception as e:
            print(f"[{coin}] binance error: {e}"); continue
        # ensure last row is the candle that just closed (its open == boundary-300)
        df = df[df.ts < boundary * 1000]
        feats = compute_features(df)
        side, rule = evaluate(rules, feats)
        if probe:
            print(f"[{coin}] features ok, signal={side or 'none'}")
        if side is None and not probe:
            continue
        mkt, slug = find_market(coin, boundary)
        if mkt is None:
            print(f"[{coin}] market not found: {slug}"); continue
        token = mkt.get(side or "Up")
        bids, asks = best_book(token)
        fill, depth = taker_fill(asks, SIZE_USD)
        if probe:
            print(f"[{coin}] {slug} best_bid={bids[0][0] if bids else None} "
                  f"best_ask={asks[0][0] if asks else None} ask_depth={depth}")
            continue
        if fill is None:
            continue
        maker_px = min((bids[0][0] + 0.01) if bids else fill, fill)
        vote, note = shadow_vote(coin, side, rule, feats, bids, asks)
        rows.append(dict(llm_vote=vote, llm_note=note,
                         ts=int(time.time()), coin=coin, window_start=boundary,
                         rule=rule, side=side,
                         best_bid=bids[0][0] if bids else "",
                         best_ask=asks[0][0] if asks else "",
                         ask_depth=depth, taker_fill=round(fill, 4),
                         maker_fill=round(maker_px, 4),
                         fee_frac=round(fee_fraction(fill), 5),
                         outcome="", won="", pnl_taker="",
                         pnl_maker_if_filled=""))
    return boundary, rows


def resolve(pending):
    done = []
    for row in pending:
        end_ms = (row["window_start"] + 300) * 1000
        if time.time() * 1000 < end_ms + 8000:
            continue
        try:
            out = candle_outcome(COINS[row["coin"]], row["window_start"] * 1000)
        except Exception:
            continue
        won = int(out == row["side"])
        for key, px in (("pnl_taker", row["taker_fill"]),
                        ("pnl_maker_if_filled", row["maker_fill"])):
            shares = SIZE_USD / px
            fee = SIZE_USD * (fee_fraction(px) if key == "pnl_taker" else 0.0)
            row[key] = round(shares * won - SIZE_USD - fee, 2)
        row["outcome"], row["won"] = out, won
        append_row(row)
        print(f"  resolved {row['coin']} {row['side']} @ {row['taker_fill']:.2f}"
              f" -> {out} ({'WIN' if won else 'LOSS'}) pnl_taker={row['pnl_taker']}")
        done.append(row)
    return [r for r in pending if r not in done]


if __name__ == "__main__":
    rules = load_rules()
    if "--report" in sys.argv:
        report(); sys.exit()
    if "--probe" in sys.argv:
        run_once(rules, int(time.time()), probe=True); sys.exit()
    print(f"paper trader running | {len(rules)} rules | ${SIZE_USD}/signal")
    pending = []
    while True:
        now = time.time()
        nxt = (int(now) // 300 + 1) * 300
        time.sleep(max(0, nxt - now) + 2.0)      # 2s after boundary
        _, rows = run_once(rules, int(time.time()))
        for r in rows:
            print(f"signal {r['coin']} {r['side']} ({r['rule']}) "
                  f"ask={r['best_ask']} fill={r['taker_fill']}")
        pending += rows
        pending = resolve(pending)
