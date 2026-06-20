"""data.py — historical OHLCV for research, from Binance Vision (static CDN, no
API key, no geo-block, no rate limits). Saves data/{SYM}_{tf}.csv, which the
harness reads for leak-safe backtests.

Usage:
    python data.py BTCUSDT 5m 2025-01 2026-05        # one timeframe
    python data.py BTCUSDT 5m 1m 2025-01 2026-05     # several at once
Lab mandate (2026-06-21): research timeframes are <=5m. Columns: ts,open,high,
low,close,volume (ms timestamps, oldest -> newest).
"""
import io
import os
import sys
import zipfile
import urllib.request

import pandas as pd

BASE = ("https://data.binance.vision/data/spot/monthly/klines/"
        "{sym}/{tf}/{sym}-{tf}-{ym}.zip")
COLS = ["ts", "open", "high", "low", "close", "volume"]
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def month_range(start_ym, end_ym):
    y, m = map(int, start_ym.split("-"))
    ey, em = map(int, end_ym.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def fetch(sym, tf, start_ym, end_ym):
    frames = []
    for ym in month_range(start_ym, end_ym):
        url = BASE.format(sym=sym, tf=tf, ym=ym)
        try:
            raw = urllib.request.urlopen(url, timeout=60).read()
        except Exception as e:
            print(f"  skip {ym}: {e}")
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            with zf.open(zf.namelist()[0]) as f:
                df = pd.read_csv(f, header=None, usecols=range(6), names=COLS)
        frames.append(df)
        print(f"  {sym} {tf} {ym}: {len(df)} bars")
    if not frames:
        raise SystemExit(f"no data for {sym} {tf} {start_ym}..{end_ym}")
    out = pd.concat(frames, ignore_index=True).sort_values("ts")
    out = out.reset_index(drop=True)
    if out["ts"].iloc[-1] > 10 ** 14:               # us -> ms (2025+ dumps)
        out["ts"] = out["ts"] // 1000
    return out.drop_duplicates("ts").reset_index(drop=True)


def save(sym, tf, start_ym, end_ym):
    os.makedirs(DATA_DIR, exist_ok=True)
    df = fetch(sym, tf, start_ym, end_ym)
    path = os.path.join(DATA_DIR, f"{sym}_{tf}.csv")
    df.to_csv(path, index=False)
    print(f"saved {path}: {len(df)} rows, "
          f"{pd.to_datetime(df.ts.iloc[0], unit='ms')} -> "
          f"{pd.to_datetime(df.ts.iloc[-1], unit='ms')}")
    return path


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 3:
        raise SystemExit(__doc__)
    sym = a[0]
    *tfs, start, end = a[1:]                          # one or more TFs, then range
    for tf in tfs:
        if tf not in ("1m", "3m", "5m"):
            print(f"  note: {tf} > 5m — research mandate is <=5m, fetching anyway")
        save(sym, tf, start, end)
