"""ideas/scrape.py — TradingView Ideas P1+P2: scrape, extract, store.
(Driven by the root entry-point `tradingview_ideas.py`; not run directly.)


Fetches public trading ideas from TradingView via Tavily, extracts trade params
(direction/entry/target/stop) from page text (and chart image if
ANTHROPIC_API_KEY is set), then stores everything in the `ideas` DB table.

Safety:
  - Checks the 20-open-trade global cap before any expensive scraping.
  - Timeframe-agnostic — trades of any timeframe are accepted (TF only sets the
    max-hold downstream; it never drops an idea).
  - No orders placed here (execution lives in ideas_exec.py).
  - Paper/demo floor unaffected.

Usage:
  python ideas_mvp.py               # scrape + store, up to 10 new ideas
  python ideas_mvp.py --limit N     # process N new ideas
  python ideas_mvp.py --probe       # dry-run: fetch + print, no DB writes
  python ideas_mvp.py --show        # print stored ideas table and exit
"""
import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

import db

# ─── Config ──────────────────────────────────────────────────────────────────
TAVILY_KEY     = os.environ.get("TAVILY_API_KEY", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
MAX_OPEN       = 20          # global cap: skip scrape when OPEN trades >= this
VISION_MODEL   = "claude-haiku-4-5-20251001"
# Timeframe-agnostic: trades of ANY timeframe are accepted (no TF cap). The TF is
# still recorded (it sets the max-hold in ideas_exec), it just never drops an idea.
MAX_TF_MIN = None        # None = no cap

# ─── DB: ideas table ─────────────────────────────────────────────────────────
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS ideas(
  id {db.SERIAL},
  ts BIGINT,
  url TEXT UNIQUE,
  author TEXT,
  symbol TEXT,
  title TEXT,
  thesis TEXT,
  boosts INTEGER,
  comments INTEGER,
  published_ts BIGINT,
  chart_image_url TEXT,
  full_text TEXT,
  direction INTEGER,
  entry DOUBLE PRECISION,
  stop DOUBLE PRECISION,
  target DOUBLE PRECISION,
  timeframe TEXT,
  basis TEXT,
  confidence DOUBLE PRECISION,
  exec_id INTEGER,
  outcome TEXT,
  ret_bps DOUBLE PRECISION,
  status TEXT
);
"""
_IDX = "CREATE UNIQUE INDEX IF NOT EXISTS ix_ideas_url ON ideas(url)"


def _init_ideas():
    c = db.conn(); cur = c.cursor()
    schema = _SCHEMA
    if not db.IS_PG:
        schema = schema.replace("DOUBLE PRECISION", "REAL").replace("BIGINT", "INTEGER")
    if db.IS_PG:
        cur.execute(schema)
    else:
        cur.executescript(schema)
    try:
        cur.execute(_IDX)
    except Exception:
        pass
    c.commit(); cur.close(); c.close()


def _known_urls():
    return {r["url"] for r in db._rows("SELECT url FROM ideas")}


def _open_count():
    """Count LIVE demo trades (status='open') — the global cap is on concurrent
    open positions, not on unresolved/awaiting-vision rows."""
    rows = db._rows("SELECT COUNT(*) AS n FROM ideas WHERE status='open'")
    return int(rows[0]["n"]) if rows else 0


def _store(idea):
    db._insert(
        "ideas",
        ["ts", "url", "author", "symbol", "title", "thesis", "boosts",
         "comments", "published_ts", "chart_image_url", "full_text",
         "direction", "entry", "stop", "target", "timeframe",
         "basis", "confidence", "outcome", "status"],
        [int(time.time()), idea["url"], idea.get("author", ""),
         idea.get("symbol"), idea.get("title", ""), idea.get("thesis", ""),
         idea.get("boosts", 0), idea.get("comments", 0),
         idea.get("published_ts", 0), idea.get("chart_image_url"),
         idea.get("full_text", ""),
         idea.get("direction", 0), idea.get("entry"), idea.get("stop"),
         idea.get("target"), idea.get("timeframe"), idea.get("basis", "unknown"),
         idea.get("confidence", 0.0), "", idea.get("status", "stored")],
    )


# ─── Tavily HTTP ─────────────────────────────────────────────────────────────
def _tavily(endpoint, payload, timeout=45):
    if not TAVILY_KEY:
        raise RuntimeError("TAVILY_API_KEY not set")
    req = urllib.request.Request(
        f"https://api.tavily.com{endpoint}",
        data=json.dumps({"api_key": TAVILY_KEY, **payload}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode()
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:400]}
    except Exception as e:
        return {"error": str(e)}


# Listing pages (order: highest idea density first)
_LISTING_PAGES = [
    "https://www.tradingview.com/markets/cryptocurrencies/ideas/",
    "https://www.tradingview.com/markets/stocks-usa/ideas/",
    "https://www.tradingview.com/markets/futures/ideas/",
    "https://www.tradingview.com/ideas/",
]

# Pattern for individual TradingView chart/idea pages
_CHART_URL_RE = re.compile(
    r"https?://(?:\w+\.)?tradingview\.com/chart/"
    r"([A-Z][A-Z0-9]{1,11})/([A-Za-z0-9_-]+)/?"
)


def _chart_image_url(slug):
    """Derive the published chart snapshot URL from an idea slug.

    TradingView serves each idea's annotated chart at
      https://s3.tradingview.com/<first-char>/<id>_big.png
    where <id> is the slug prefix before the first '-'
    (e.g. 'kPHQGAnQ-BTCUSDT' -> 'kPHQGAnQ' -> .../k/kPHQGAnQ_big.png).
    No page fetch needed — this is the keystone for cheap vision."""
    idea_id = slug.split("-")[0]
    if not idea_id:
        return None
    # folder is the LOWERCASED first char of the id (the id keeps its own case);
    # uppercase-folder URLs 403.
    return f"https://s3.tradingview.com/{idea_id[0].lower()}/{idea_id}_big.png"


def _parse_listing_raw(raw):
    """Parse Tavily-extracted listing markdown into idea metas.

    Each idea in the markdown looks like:
      [SYMBOL](chart_url)[Full analysis...](chart_url)
      [by AUTHOR](/u/AUTHOR/)
      [N](chart_url#chart-view-comment-form "Comment")
    """
    link_re   = re.compile(r"\[([^\]]*?)\]\((" + _CHART_URL_RE.pattern + r")[^)]*\)")
    author_re = re.compile(r"\[by ([^\]\\]+)\]\(/u/[^/]+/\)")

    by_url = {}
    for m in link_re.finditer(raw):
        text = m.group(1).strip()
        url  = m.group(2)
        nm   = _CHART_URL_RE.search(url)
        if not nm:
            continue
        symbol    = nm.group(1)
        slug      = nm.group(2)
        chart_url = f"https://www.tradingview.com/chart/{symbol}/{slug}/"
        rec = by_url.setdefault(chart_url,
                                dict(symbol=symbol, slug=slug, texts=[],
                                     author="", comments=0))
        if text:
            rec["texts"].append(text)

    for chart_url, rec in by_url.items():
        idx = raw.find(chart_url)
        if idx < 0:
            continue
        vicinity = raw[idx: idx + 600]
        am = author_re.search(vicinity)
        if am:
            rec["author"] = am.group(1).replace("\\", "").strip()
        cm = re.search(r"\[(\d+)\]\([^)]*#chart-view-comment-form", vicinity)
        if cm:
            rec["comments"] = int(cm.group(1))

    ideas = []
    for chart_url, rec in by_url.items():
        texts    = sorted(rec["texts"], key=len, reverse=True)
        analysis = texts[0] if texts else ""
        title    = texts[-1] if len(texts) > 1 else analysis[:80]
        ideas.append({
            "url":             chart_url,
            "symbol":          rec["symbol"],
            "title":           title[:120],
            "snippet":         analysis[:500],
            "author":          rec["author"],
            "comments":        rec["comments"],
            "chart_image_url": _chart_image_url(rec["slug"]),
            "published_date":  None,
        })
    return ideas


def _fetch_listing_urls(n=30):
    """Tavily extract on TV listing pages -> list of idea metas with text."""
    print(f"  [listing] Tavily extract on {len(_LISTING_PAGES)} listing pages...")
    d = _tavily("/extract", {"urls": _LISTING_PAGES, "include_images": False})
    if d.get("error"):
        print(f"  [listing] error: {d}")
        return []

    seen = set(); out = []
    for r in d.get("results", []):
        raw = r.get("raw_content") or ""
        for idea in _parse_listing_raw(raw):
            url = idea["url"]
            if url in seen:
                continue
            seen.add(url)
            out.append(idea)
            if len(out) >= n:
                break
        if len(out) >= n:
            break

    print(f"  [listing] {len(out)} ideas parsed from listing pages")
    return out


# ─── Per-idea page ────────────────────────────────────────────────────────────
def _fetch_page(url):
    """Tavily extract: full text + chart image URLs for one idea page."""
    d = _tavily("/extract", {"urls": [url], "include_images": True})
    if d.get("error"):
        print(f"    [extract err] {d}")
        return None
    results = d.get("results", [])
    if not results:
        return None
    r = results[0]
    return {
        "raw": r.get("raw_content", "") or "",
        "images": r.get("images", []) or [],
    }


# ─── Text extraction ──────────────────────────────────────────────────────────
def _parse_price(s):
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def _extract_text(url, title, snippet, raw):
    """Heuristic text parser → direction/entry/target/stop/TF/symbol."""
    text = f"{title}\n{snippet}\n{raw}"
    tl = text.lower()

    # Symbol: from URL first (e.g. /chart/BTCUSDT/slug)
    symbol = None
    m = re.search(r'/chart/([A-Z]{2,12})/', url)
    if m:
        symbol = m.group(1)
    if not symbol:
        # scan title for known tickers
        m = re.search(
            r'\b(BTC(?:USDT?)?|ETH(?:USDT?)?|SOL(?:USDT?)?|XRP(?:USDT?)?|'
            r'DOGE(?:USDT?)?|AAPL|TSLA|NVDA|MSFT|AMZN|GOOGL|META|SPY|QQQ|'
            r'GOLD?|US30|NAS100|DXY|EUR/?USD|GBP/?USD)\b',
            text.upper())
        if m:
            symbol = m.group(1)

    # Direction
    lwords = ["long", " buy ", "bullish", "upside", "breakout", "buy setup", "going up"]
    swords = ["short", " sell ", "bearish", "downside", "breakdown", "sell setup", "going down"]
    ls = sum(1 for w in lwords if w in tl)
    ss = sum(1 for w in swords if w in tl)
    direction = 1 if ls > ss else (-1 if ss > ls else 0)
    confidence = 0.3 + min(0.25, abs(ls - ss) * 0.08)

    # Price levels — explicit labels first, then natural-language phrases
    def _pv(raw_s, suffix=""):
        v = _parse_price(raw_s)
        if v and v > 0:
            return v * 1000 if suffix.lower() == "k" else v
        return None

    entry = target = stop = None
    explicit_patterns = [
        (r'(?:entry|entering?|enter\s+(?:at|near)|buy\s+at|sell\s+at)[\s:@]+\$?(\d[\d,\.]+)(k?)', "entry"),
        (r'(?:target|tp|take[\s-]?profit)[\s:#\d]*[\s:=]+\$?(\d[\d,\.]+)(k?)', "target"),
        (r'(?:stop(?:[\s-]?loss)?|\bsl\b)[\s:=]+\$?(\d[\d,\.]+)(k?)', "stop"),
    ]
    natural_patterns = [
        (r'(?:first\s+(?:downside\s+)?target|price\s+target|next\s+target)'
         r'\s+(?:comes?\s+in\s+)?(?:around|at|near|toward|to)\s+\$?(\d[\d,\.]+)(k?)', "target"),
        (r'(?:stop\s+loss|invalidation|stoploss)\s+(?:is\s+)?(?:at|around|near|above|below)'
         r'\s+\$?(\d[\d,\.]+)(k?)', "stop"),
        (r'(?:long|buy)\s+(?:at|near|around)\s+\$?(\d[\d,\.]+)(k?)', "entry"),
        (r'(?:short|sell)\s+(?:at|near|around)\s+\$?(\d[\d,\.]+)(k?)', "entry"),
        (r'resistance\s+(?:at|around|near)\s+\$?(\d[\d,\.]+)(k?)', "target"),
        (r'support\s+(?:at|around|near)\s+\$?(\d[\d,\.]+)(k?)', "stop"),
    ]
    for pat, key in explicit_patterns + natural_patterns:
        if locals()[key] is not None:
            continue  # already filled by earlier (higher-priority) pattern
        mc = re.search(pat, tl)
        if mc:
            v = _pv(mc.group(1), mc.group(2))
            if v:
                if key == "entry":    entry  = v
                elif key == "target": target = v
                elif key == "stop":   stop   = v

    # Timeframe (longest match wins: check 4h before h, etc.)
    timeframe = None
    for tf in ["1w", "1d", "4h", "2h", "1h", "30m", "15m", "5m", "3m", "1m"]:
        if tf in tl:
            timeframe = tf
            break

    if entry or target or stop:
        confidence = min(0.85, confidence + 0.2)

    return dict(symbol=symbol, direction=direction, entry=entry, target=target,
                stop=stop, timeframe=timeframe, basis="text", confidence=confidence)


# ─── Vision extraction (Claude) ───────────────────────────────────────────────
def _pick_chart_img(images):
    for img in images:
        u = img if isinstance(img, str) else (img.get("url") or "")
        if any(x in u.lower() for x in ["chart", "snapshot", "tradingview", "s3"]):
            return u
    return (images[0] if isinstance(images[0], str) else images[0].get("url")) if images else None


def _vision_extract(img_url, title, snippet):
    """Claude reads the chart image → structured trade params (or None on failure)."""
    if not ANTHROPIC_KEY or not img_url:
        return None
    try:
        img_data = urllib.request.urlopen(img_url, timeout=25).read()
    except Exception as e:
        print(f"    [vision] image fetch failed: {e}")
        return None
    mt = "image/png" if img_url.lower().endswith(".png") else "image/jpeg"
    b64 = base64.b64encode(img_data).decode()

    prompt = (
        'Analyze this TradingView chart. Extract the trade setup drawn on it.\n'
        'Return ONLY valid JSON (no other text):\n'
        '{"symbol":"TICKER or null","direction":1or-1or0,'
        '"entry":price_or_null,"target":price_or_null,"stop":price_or_null,'
        '"timeframe":"4h|1h|15m|5m|etc or null",'
        '"basis":"chart","confidence":0.0to1.0}\n'
        '1=long/buy, -1=short/sell, 0=unclear. '
        'Set price fields null if not clearly marked.\n\n'
        f'Post: {title}\n{snippet[:300]}'
    )
    payload = {
        "model": VISION_MODEL,
        "max_tokens": 350,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "x-api-key": ANTHROPIC_KEY,
                 "anthropic-version": "2023-06-01"})
    try:
        raw = urllib.request.urlopen(req, timeout=90).read().decode()
        resp = json.loads(raw)
        text = resp["content"][0]["text"]
        mc = re.search(r'\{.*?\}', text, re.DOTALL)
        if mc:
            return json.loads(mc.group())
    except Exception as e:
        print(f"    [vision] extract failed: {e}")
    return None


# ─── Timeframe guard ─────────────────────────────────────────────────────────
def _tf_minutes(tf):
    """Parse a timeframe string ('15m','1h','4h','1d','1w') to minutes, or None."""
    if not tf:
        return None
    m = re.match(r'(\d+)\s*([mhdw])', tf.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 1, "h": 60, "d": 1440, "w": 10080}[unit]


def _tf_allowed(tf):
    """Timeframe-agnostic — every timeframe is allowed (MAX_TF_MIN=None). Kept as
    a function so a cap can be reinstated by setting MAX_TF_MIN to a minute value."""
    if MAX_TF_MIN is None:
        return True
    mins = _tf_minutes(tf)
    return mins is None or mins <= MAX_TF_MIN


# ─── Core: process one idea ───────────────────────────────────────────────────
def _process(meta, probe=False):
    url     = meta["url"]
    title   = meta.get("title", "")
    # snippet already contains the full analysis text from the listing page
    snippet = meta.get("snippet", "")
    print(f"  -> {url[:90]}")

    # Text extraction runs directly on listing-provided content.
    params = _extract_text(url, title, snippet, "")

    # Chart image is derived from the slug — no page fetch needed.
    chart_img = meta.get("chart_image_url")

    # Vision: only when an API key is configured. WITHOUT a key the idea is
    # stored basis='text' with status='needs_vision', and a human (Claude Code,
    # see tradingview_automation_run.md) reads the chart image and fills the
    # levels via `--set-levels`. This is the API-ready manual path.
    if ANTHROPIC_KEY and chart_img:
        print("    [vision] reading chart image...")
        vp = _vision_extract(chart_img, title, snippet)
        if vp and vp.get("basis") == "chart":
            params.update({
                "direction":  vp.get("direction",  params["direction"]),
                "entry":      vp.get("entry",       params["entry"]),
                "target":     vp.get("target",      params["target"]),
                "stop":       vp.get("stop",        params["stop"]),
                "timeframe":  vp.get("timeframe",   params["timeframe"]),
                "basis":      "chart",
                "confidence": vp.get("confidence",  params["confidence"]),
            })
            if vp.get("symbol"):
                params["symbol"] = vp["symbol"]

    # Timeframe guard
    tf = params.get("timeframe")
    if not _tf_allowed(tf):
        print(f"    [skip] TF={tf} > 4h")
        return False

    # status: 'extracted' if we have a tradeable bracket (dir + 2 of 3 levels);
    # else 'needs_vision' — a human/VLM should read the chart image to fill it.
    has_levels = sum(x is not None for x in
                     (params["entry"], params["target"], params["stop"])) >= 2
    status = "extracted" if (params["direction"] != 0 and has_levels) else "needs_vision"

    idea = {
        "url":           url,
        "title":         title,
        "thesis":        snippet[:500],
        "author":        meta.get("author", ""),
        "symbol":        params.get("symbol"),
        "boosts":        meta.get("boosts", 0),
        "comments":      meta.get("comments", 0),
        "published_ts":  0,
        "chart_image_url": chart_img,
        "full_text":     snippet[:5000],
        "direction":     params["direction"],
        "entry":         params["entry"],
        "stop":          params["stop"],
        "target":        params["target"],
        "timeframe":     params["timeframe"],
        "basis":         params["basis"],
        "confidence":    params["confidence"],
        "status":        status,
    }

    d_str = "LONG " if idea["direction"] == 1 else ("SHORT" if idea["direction"] == -1 else "???  ")
    sym   = idea["symbol"] or "?"
    print(f"    {d_str} {sym:<12}  entry={idea['entry']}  tp={idea['target']}  "
          f"sl={idea['stop']}  tf={idea['timeframe']}  "
          f"basis={idea['basis']}  conf={idea['confidence']:.2f}")

    if not probe:
        try:
            _store(idea)
            print("    [ok] stored")
        except Exception as e:
            print(f"    [store err] {e}")
            return False

    return True


# ─── Table display ────────────────────────────────────────────────────────────
def _show():
    rows = db._rows(
        "SELECT id, ts, url, symbol, direction, entry, target, stop, "
        "timeframe, basis, confidence, status FROM ideas ORDER BY ts DESC LIMIT 50")
    if not rows:
        print("No ideas stored yet.")
        return
    print(f"\n{'ID':>4}  {'Symbol':<12}  {'Dir':<5}  {'Entry':>11}  {'Target':>11}  "
          f"{'Stop':>11}  {'TF':<5}  {'Basis':<9}  {'Conf':>5}  {'Status':<8}  URL")
    print("-" * 140)
    for r in rows:
        d = "LONG" if r["direction"] == 1 else ("SHORT" if r["direction"] == -1 else "?")
        print(
            f"{r['id']:>4}  {(r['symbol'] or '?'):<12}  {d:<5}  "
            f"{(r['entry']  or 0):>11.4f}  {(r['target'] or 0):>11.4f}  "
            f"{(r['stop']   or 0):>11.4f}  {(r['timeframe'] or '?'):<5}  "
            f"{(r['basis']  or '?'):<9}  {(r['confidence'] or 0):>5.2f}  "
            f"{(r['status'] or ''):<8}  {r['url'][:60]}"
        )


# ─── Manual vision support (Claude Code reads charts, no API) ─────────────────
def _list_vision(limit=50):
    """Print ideas awaiting a chart read as a compact JSON list. The runbook
    (tradingview_automation_run.md) feeds these image URLs to Claude Code, which
    reads each chart and writes levels back via --set-levels."""
    rows = db._rows(
        "SELECT id, symbol, direction, chart_image_url, thesis, timeframe "
        "FROM ideas WHERE status='needs_vision' "
        f"ORDER BY ts DESC LIMIT {int(limit)}")
    out = [{
        "id":              r["id"],
        "symbol":          r["symbol"],
        "current_dir":     r["direction"],
        "timeframe":       r["timeframe"],
        "chart_image_url": r["chart_image_url"],
        "thesis":          (r["thesis"] or "")[:240],
    } for r in rows]
    print(json.dumps(out, indent=2))
    print(f"\n{len(out)} idea(s) awaiting a chart read.", flush=True)


def _set_levels(idea_id, direction=None, entry=None, target=None, stop=None,
                timeframe=None, basis="chart", confidence=None,
                status="extracted"):
    """Write levels read from a chart (by Claude Code or a VLM) back to an idea.
    Drops the idea if its timeframe is >4H (the runbook's hard cap)."""
    if timeframe is not None and not _tf_allowed(timeframe):
        db._rows(f"SELECT 1")  # no-op to keep a connection-less path consistent
        c = db.conn(); cur = c.cursor()
        cur.execute(f"UPDATE ideas SET status='dropped_tf' WHERE id={db.PH}",
                    (idea_id,))
        c.commit(); cur.close(); c.close()
        print(f"idea {idea_id}: TF={timeframe} > 4H -> dropped")
        return

    sets, vals = [], []
    for col, v in [("direction", direction), ("entry", entry),
                   ("target", target), ("stop", stop),
                   ("timeframe", timeframe), ("basis", basis),
                   ("confidence", confidence), ("status", status)]:
        if v is not None:
            sets.append(f"{col}={db.PH}")
            vals.append(v)
    if not sets:
        print("nothing to set")
        return
    vals.append(idea_id)
    c = db.conn(); cur = c.cursor()
    cur.execute(f"UPDATE ideas SET {','.join(sets)} WHERE id={db.PH}", vals)
    c.commit(); cur.close(); c.close()
    print(f"idea {idea_id} updated: "
          + " ".join(f"{k}={v}" for k, v in zip(
              ["dir", "entry", "target", "stop", "tf", "basis", "conf", "status"],
              [direction, entry, target, stop, timeframe, basis, confidence, status])
              if v is not None))


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=10,
                    help="max new ideas to process (default 10)")
    ap.add_argument("--probe", action="store_true",
                    help="dry-run: scrape + extract, no DB writes")
    ap.add_argument("--show", action="store_true",
                    help="print stored ideas table and exit")
    ap.add_argument("--list-vision", action="store_true",
                    help="print ideas awaiting a manual chart read (JSON)")
    # manual chart-read writeback (Claude Code / future VLM):
    ap.add_argument("--set-levels", type=int, metavar="ID",
                    help="write chart-read levels back to idea ID")
    ap.add_argument("--direction", type=int, choices=[-1, 0, 1])
    ap.add_argument("--entry", type=float)
    ap.add_argument("--target", type=float)
    ap.add_argument("--stop", type=float)
    ap.add_argument("--tf", type=str, help="timeframe e.g. 1h, 4h, 15m")
    ap.add_argument("--basis", type=str, default="chart")
    ap.add_argument("--confidence", type=float)
    args = ap.parse_args()

    db.init()
    _init_ideas()

    if args.show:
        _show()
        return

    if args.list_vision:
        _list_vision()
        return

    if args.set_levels is not None:
        _set_levels(args.set_levels, direction=args.direction, entry=args.entry,
                    target=args.target, stop=args.stop, timeframe=args.tf,
                    basis=args.basis, confidence=args.confidence)
        return

    n_open = _open_count() if not args.probe else 0
    if n_open >= MAX_OPEN:
        print(f"[cap] {n_open}/{MAX_OPEN} open ideas — skipping scrape (global cap).")
        return

    tv_status  = "ok" if TAVILY_KEY    else "MISSING (TAVILY_API_KEY)"
    vis_status = f"Claude ({VISION_MODEL})" if ANTHROPIC_KEY else "text-only (no ANTHROPIC_API_KEY)"
    db_status  = "Neon/Postgres" if db.IS_PG else "SQLite local"
    print(f"[ideas_mvp]  open={n_open}/{MAX_OPEN}  db={db_status}  "
          f"tavily={tv_status}  vision={vis_status}")

    seen       = _known_urls() if not args.probe else set()
    candidates = _fetch_listing_urls(n=max(30, args.limit * 3))
    new_ideas  = [c for c in candidates if c["url"] not in seen]
    print(f"[ideas_mvp]  candidates={len(candidates)}  new={len(new_ideas)}  "
          f"limit={args.limit}")
    if not new_ideas:
        print("[ideas_mvp]  nothing new — done.")
        return

    stored = 0
    for meta in new_ideas[:args.limit]:
        try:
            if _process(meta, probe=args.probe):
                stored += 1
        except Exception as e:
            print(f"  [error] {meta.get('url','?')}: {e}")
        time.sleep(1.2)   # Tavily rate limit headroom

    label = "(probe — no writes)" if args.probe else "stored"
    print(f"\n[ideas_mvp]  done — {stored}/{min(len(new_ideas), args.limit)} {label}")

    if not args.probe:
        print()
        _show()


if __name__ == "__main__":
    main()
