"""adapters.data.deribit — Deribit options market data (testnet).

A leak-free research surface for the crypto-options domain: the live option chain
with mark price + Deribit's implied vol + greeks, plus the index spot. A vol-model
strategy compares our realized-vol estimate to Deribit's implied/mark to find
mispriced contracts (same idea as kalshi_crypto_model, on real options).

  index_price(currency)        spot index (e.g. btc_usd)
  option_instruments(currency) live (non-expired) option contracts + parsed meta
  ticker(instrument)           mark price, mark_iv, bid/ask, greeks, underlying
  near_atm(currency, ...)      the nearest-expiry options closest to ATM
"""
from datetime import datetime, timezone

from adapters.deribit_client import Deribit

_D = None


def client():
    global _D
    if _D is None:
        _D = Deribit()
    return _D


def index_price(currency="BTC"):
    return client().public("get_index_price",
                           index_name=f"{currency.lower()}_usd")["index_price"]


def _parse(name):
    """BTC-17JUN26-57000-C -> dict(expiry_ms, strike, cp). Deribit option names."""
    try:
        _, exp, strike, cp = name.split("-")
        dt = datetime.strptime(exp, "%d%b%y").replace(tzinfo=timezone.utc)
        return {"expiry": exp, "expiry_ms": int(dt.timestamp() * 1000),
                "strike": float(strike), "cp": cp}      # cp: 'C' or 'P'
    except Exception:
        return {}


def option_instruments(currency="BTC"):
    out = []
    for m in client().public("get_instruments", currency=currency, kind="option",
                             expired="false"):     # Deribit wants the string
        meta = _parse(m["instrument_name"])
        out.append({"instrument_name": m["instrument_name"],
                    "strike": meta.get("strike"), "cp": meta.get("cp"),
                    "expiry": meta.get("expiry"),
                    "expiry_ms": meta.get("expiry_ms") or m.get("expiration_timestamp"),
                    "tick_size": m.get("tick_size"),
                    "min_trade_amount": m.get("min_trade_amount")})
    return out


def ticker(instrument_name):
    t = client().public("ticker", instrument_name=instrument_name)
    g = t.get("greeks", {}) or {}
    return {"instrument_name": instrument_name,
            "mark_price": t.get("mark_price"),          # in underlying (e.g. BTC)
            "mark_iv": t.get("mark_iv"),
            "best_bid": t.get("best_bid_price"), "best_ask": t.get("best_ask_price"),
            "underlying_price": t.get("underlying_price"),
            "index_price": t.get("index_price"),
            "delta": g.get("delta"), "vega": g.get("vega"), "theta": g.get("theta")}


def near_atm(currency="BTC", n_strikes=3, cp=None):
    """Options on the nearest expiry whose strike is closest to spot. Returns up to
    n_strikes per call/put (or only `cp` if given). Pure data — no lookahead."""
    spot = index_price(currency)
    insts = [i for i in option_instruments(currency) if i.get("expiry_ms")]
    if not insts:
        return []
    near_exp = min(i["expiry_ms"] for i in insts)
    chain = [i for i in insts if i["expiry_ms"] == near_exp
             and (cp is None or i["cp"] == cp)]
    chain.sort(key=lambda i: abs((i["strike"] or 0) - spot))
    out = []
    seen = set()
    for i in chain:
        key = (i["strike"], i["cp"])
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
        if len({k[0] for k in seen}) >= n_strikes and cp is not None:
            break
        if len(out) >= n_strikes * (1 if cp else 2):
            break
    return out


if __name__ == "__main__":
    print("BTC index:", index_price("BTC"))
    ins = option_instruments("BTC")
    print("live BTC options:", len(ins))
    atm = near_atm("BTC", n_strikes=2)
    for i in atm[:4]:
        t = ticker(i["instrument_name"])
        print(f"  {i['instrument_name']}: mark={t['mark_price']} iv={t['mark_iv']} "
              f"delta={t['delta']}")
