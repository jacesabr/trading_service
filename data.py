"""
data.py — Download historical OHLCV from Binance Vision (static CDN, no API
key, no geo-block, no rate limits). Monthly zipped klines.

Usage:
    python3 data.py BTCUSDT 1h 5m 2025-01 2026-05
saves BTCUSDT_1h.csv and BTCUSDT_5m.csv with columns ts,open,high,low,close,volume
"""
import io
import os
import sys
import zipfile
import urllib.request
import pandas as pd

BASE = "https://data.binance.vision/data/spot/monthly/klines/{sym}/{tf}/{sym}-{tf}-{ym}.zip"
COLS = ["ts", "open", "high", "low", "close", "volume"]


def month_range(start_ym: str, end_ym: str):
    y, m = map(int, start_ym.split("-"))
    ey, em = map(int, end_ym.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def fetch(sym: str, tf: str, start_ym: str, end_ym: str) -> pd.DataFrame:
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
    out = pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)
    # Binance switched ts to microseconds in 2025 data dumps for some feeds; normalize to ms
    if out["ts"].iloc[-1] > 10 ** 14:
        out["ts"] = out["ts"] // 1000
    return out.drop_duplicates("ts").reset_index(drop=True)


if __name__ == "__main__":
    sym, chart_tf, ltf, start, end = sys.argv[1:6]
    os.makedirs("data", exist_ok=True)
    for tf in (chart_tf, ltf):
        df = fetch(sym, tf, start, end)
        path = f"data/{sym}_{tf}.csv"               # data dumps live in data/
        df.to_csv(path, index=False)
        print(f"saved {path}: {len(df)} rows, "
              f"{pd.to_datetime(df.ts.iloc[0], unit='ms')} -> "
              f"{pd.to_datetime(df.ts.iloc[-1], unit='ms')}")
