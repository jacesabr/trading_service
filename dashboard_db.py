"""
dashboard_db.py — DB-backed monitor for the full strategy battery.

One card per strategy in strategies.STRATEGIES, each showing:
  * honest status badge (LIVE candidate / paper / DEAD-control)
  * plain-English method (no thresholds — the edge stays private)
  * live status line (last signal age, resolved + pending counts)
  * KPIs + an equity curve (cum P&L $ for meanrev, cum bps for the rest)
  * a collapsible recent-trades log

meanrev reads the Polymarket `bets` table; every other strategy reads `trades`
(binary next-bar predictions and SL/TP brackets both land there).

Run: python dashboard_db.py  ->  http://localhost:8050
"""
import json
import os
import time

import numpy as np
from flask import Flask, jsonify, Response, request

import db
from rlab import registry

SIZE_USD = float(os.environ.get("SIZE_USD", "100"))

# Public payloads expose RESULTS + identity only. Everything that reveals HOW an
# edge works or how it was found (method/risk text, signal params/rules, the
# promotion gate, provenance/hypothesis/research refs, the experiment ledger) is
# admin-only — gated by ADMIN_PASSWORD. The track record itself (hit rate, equity,
# recent fills, entry prices) IS public: it is the verifiable credibility.
PUBLIC_KEYS = {
    "label", "status", "kind", "venue", "symbols", "domain", "lifecycle",
    "role", "pending", "last_age_min", "n", "hit", "unit", "equity", "exp_bps",
    "net_bps", "ev", "avg_entry", "rw_base", "edge_pp", "recent", "venues",
    "spread_bps", "spread_n", "fee_bps", "cal",
    "platform", "mode", "pnl_total", "rank_score", "confidence",
    "group", "real",
}


def _public(card):
    return {k: v for k, v in card.items() if k in PUBLIC_KEYS}


def _admin_ok():
    """HTTP Basic gate. Returns (True, None) if authorized, else (False, resp).
    If ADMIN_PASSWORD is unset, admin endpoints are disabled (not wide open)."""
    pw = os.environ.get("ADMIN_PASSWORD")
    if not pw:
        return False, (jsonify({"error": "admin not configured"}), 503)
    auth = request.authorization
    user = os.environ.get("ADMIN_USER", "admin")
    if not auth or auth.username != user or auth.password != pw:
        return False, Response(
            "admin login required", 401,
            {"WWW-Authenticate": 'Basic realm="strategy-lab admin"'})
    return True, None
# Real cost model for spot paper strategies = measured spread (per trade, from
# Binance book at signal time) + a transparent round-trip fee. FEE_BPS is the
# ONLY assumption and it's tunable: default 4 bps round-trip ≈ 2 bps/side, a
# low-fee-venue / maker-ish taker for liquid crypto. Set FEE_BPS=0 for a pure
# maker-rebate view. Net = gross − avg_measured_spread − FEE_BPS.
FEE_BPS = float(os.environ.get("FEE_BPS", "4"))
app = Flask(__name__)
app.json.sort_keys = False        # preserve our most->least profitable ordering
db.init()


def _spread_of(row):
    d = row.get("detail")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            d = {}
    if isinstance(d, dict) and isinstance(d.get("spread_bps"), (int, float)):
        return float(d["spread_bps"])
    return None


def _age_min(ts, now):
    return round((now - int(ts)) / 60, 1) if ts else None


PLATFORM = {"polymarket": "Polymarket", "kalshi": "Kalshi", "alpaca": "Alpaca",
            "spot": "Binance spot", "oanda": "OANDA"}
DATASRC = {"crypto": "Binance", "prediction_market": "Kalshi", "equity": "Alpaca"}


def _why(meta, card):
    """Plain-language note on why this strategy's numbers can be trusted, shown
    per card. Built from how it actually resolves + the sample's honest caveats."""
    n = card.get("n", 0)
    if not n:
        return "No resolved trades yet — numbers appear once positions settle."
    venue = meta.get("venue", ""); domain = meta.get("domain", "")
    if venue == "kalshi":
        how = "Each call is settled by Kalshi's official finalized result"
    elif venue == "polymarket":
        how = ("Resolved on the same Binance 5m candle Polymarket settles on")
    else:
        how = f"Resolved on real {DATASRC.get(domain, venue)} bars"
    base = (f"Paper, real data. {how}. The signal is computed only from "
            f"already-closed bars and scored on a later bar, so no future "
            f"information can leak in. n={n}.")
    if n < 100:
        base += " Small sample — treat as indicative."
    elif domain == "equity":
        base += (" Samples are somewhat correlated (nearby signals in one move), "
                 "so trust the hit rate more than the exact bps until n grows.")
    return base


def _build_cards():
    """Full (admin-grade) card per strategy: results + identity + the private
    HOW (method/risk/signal/gate/provenance). Public route redacts via _public."""
    db.init()
    now = int(time.time())
    bets = db.recent_bets(800)
    trades = db.recent_trades_capped(2000)        # per-strategy: no 0-trade crowd-out
    execs = db.recent_executions(2000)
    act = db.activity()

    out = {"last_update": now, "strategies": {}}
    for name, meta in registry.registry().items():
        a = act.get(name, {})
        card = dict(label=meta.get("label", name), status=meta.get("status", ""),
                    kind=meta.get("kind", ""), venue=meta.get("venue", ""),
                    symbols=meta.get("symbols", ""), domain=meta.get("domain", ""),
                    lifecycle=meta.get("lifecycle", ""), role=meta.get("role", ""),
                    method=meta.get("method", ""), risk=meta.get("risk", ""),
                    # admin-only HOW + provenance (redacted from public):
                    signal=meta.get("signal", {}), gate=meta.get("gate", {}),
                    exec_model=meta.get("exec_model", ""),
                    provenance=meta.get("provenance", {}),
                    platform=PLATFORM.get(meta.get("venue", ""), meta.get("venue", "")),
                    mode="paper",               # all strategies are paper for now
                    pending=a.get("pending", 0),
                    last_age_min=_age_min(a.get("last_ts"), now),
                    n=0, recent=[], pnl_total=0, rank_score=-1e12)

        if meta["venue"] == "polymarket":           # meanrev -> bets
            rows = [b for b in bets if b["strategy"] == name]
            res = [b for b in rows if b["outcome"]]
            if res:
                res_sorted = sorted(res, key=lambda b: b["ts"])
                won = np.array([b["won"] for b in res], float)
                ent = np.array([b["entry_price"] for b in res], float)
                pnl = np.array([b["pnl"] for b in res_sorted], float)
                cal = {}
                for b in res:
                    k = round(b["entry_price"] * 20) / 20
                    cal.setdefault(k, []).append(b["won"])
                card.update(
                    n=len(res), hit=round(float(won.mean()), 4),
                    avg_entry=round(float(ent.mean()), 3),
                    ev=round(float(pnl.mean()) / SIZE_USD, 4),
                    equity=np.round(np.cumsum(pnl), 2).tolist(), unit="$",
                    cal=[{"p": round(float(k), 2),
                          "win": round(float(np.mean(v)), 3), "n": len(v)}
                         for k, v in sorted(cal.items()) if len(v) >= 5],
                    recent=[dict(t=b["ts"], symbol=b["symbol"], side=b["side"],
                                 entry=round(b["entry_price"], 3),
                                 outcome=b["outcome"], pnl=b["pnl"])
                            for b in sorted(res, key=lambda b: b["ts"],
                                            reverse=True)[:25]])
                card["pnl_total"] = round(float(pnl.sum()), 2)   # total $ P&L
                card["rank_score"] = round(float(pnl.mean()) / SIZE_USD * 1e4, 1)
        else:                                        # everything else -> trades
            rows = [t for t in trades if t["strategy"] == name]
            res = [t for t in rows if t["outcome"]]
            if res:
                res_sorted = sorted(res, key=lambda t: t["ts"])
                won = np.array([t["won"] for t in res], float)
                ret = np.array([t["ret_bps"] for t in res_sorted], float)
                gross = float(ret.mean())
                spreads = [s for s in (_spread_of(t) for t in res) if s is not None]
                avg_spread = round(float(np.mean(spreads)), 2) if spreads else None
                cost = (avg_spread or 0.0) + FEE_BPS
                card.update(
                    n=len(res), hit=round(float(won.mean()), 4),
                    exp_bps=round(gross, 1),
                    net_bps=round(gross - cost, 1),
                    spread_bps=avg_spread, fee_bps=FEE_BPS,
                    spread_n=len(spreads),
                    equity=np.round(np.cumsum(ret), 1).tolist(), unit="bps",
                    recent=[dict(t=t["ts"], symbol=t["symbol"], side=t["side"],
                                 entry=t["entry"], exit=t["exit"],
                                 outcome=t["outcome"], ret_bps=t["ret_bps"])
                            for t in sorted(res, key=lambda t: t["ts"],
                                            reverse=True)[:25]])
                # bracket only: edge vs random-walk first-passage baseline.
                # rw_base = P(touch target before stop) under a driftless walk =
                # stop_dist / (stop_dist + target_dist). win >> rw_base here is
                # mostly the intrabar-touch-vs-close asymmetry, not real edge.
                br = [t for t in res if t["target"] is not None
                      and t["stop"] is not None and t["entry"]]
                rw = [abs(t["entry"] - t["stop"]) /
                      (abs(t["entry"] - t["stop"]) + abs(t["target"] - t["entry"]))
                      for t in br
                      if abs(t["entry"] - t["stop"]) + abs(t["target"] - t["entry"]) > 0]
                if rw:
                    win_br = float(np.mean([t["won"] for t in br]))
                    rwm = float(np.mean(rw))
                    card.update(rw_base=round(rwm, 3),
                                edge_pp=round((win_br - rwm) * 100, 1))
                net_cum = float(ret.sum()) - len(res) * cost
                card["pnl_total"] = round(net_cum, 1)            # cumulative net bps
                card["rank_score"] = round(gross - cost, 2)      # net bps / trade

        # real-venue executions for this strategy (fill already crossed the
        # venue's real spread -> net only deducts the fee)
        ex = [e for e in execs if e["strategy"] == name]
        if ex:
            vmap = {}
            for e in ex:
                vmap.setdefault(e["venue"], []).append(e)
            card["venues"] = {
                v: dict(n=len(rs),
                        hit=round(float(np.mean([r["won"] for r in rs])), 3),
                        net_bps=round(float(np.mean([r["ret_bps"] for r in rs]))
                                      - FEE_BPS, 1))
                for v, rs in vmap.items()}
        # REAL broker orders (alpaca_exec): actual placed + filled demo trades
        # (ref='order:<id>'), as opposed to quote-cross snapshots. A strategy
        # with these is a live system "making real demo trades" -> it floats to
        # the top group. ret_bps here is the realized round-trip (the real spread
        # is already in the fills), so it IS the net — no extra cost deducted.
        real = [e for e in ex if (e.get("ref") or "").startswith("order:")]
        if real:
            rr = np.array([e["ret_bps"] for e in real], float)
            rw = np.array([e["won"] for e in real], float)
            card["real"] = dict(n=len(real), venue=real[0]["venue"],
                                hit=round(float(rw.mean()), 4),
                                net_bps=round(float(rr.mean()), 1),
                                pnl=round(float(rr.sum()), 1))
            card["rank_score"] = round(float(rr.mean()), 2)   # rank live by REAL P&L
        card["group"] = "live" if real else "paper"
        card["confidence"] = _why(meta, card)
        out["strategies"][name] = card
    # Systems making REAL broker demo trades (real fills) float to the TOP;
    # within each group, sort most -> least profitable.
    items = list(out["strategies"].items())
    key = lambda kv: kv[1].get("rank_score", -1e12)
    live = sorted([kv for kv in items if kv[1].get("group") == "live"],
                  key=key, reverse=True)
    paper = sorted([kv for kv in items if kv[1].get("group") != "live"],
                   key=key, reverse=True)
    out["strategies"] = dict(live + paper)
    out["live_count"] = len(live)
    return out


@app.route("/api/stats")
def stats():
    """PUBLIC — results + identity only; the HOW is redacted."""
    full = _build_cards()
    full["strategies"] = {n: _public(c) for n, c in full["strategies"].items()}
    return jsonify(full)


@app.route("/api/admin/stats")
def admin_stats():
    """ADMIN — full cards + experiment ledger + version history + lessons."""
    ok, resp = _admin_ok()
    if not ok:
        return resp
    full = _build_cards()
    for name, card in full["strategies"].items():
        card["experiments"] = db.experiments(name, limit=10)
        card["versions"] = db.versions(name, limit=10)
    full["lessons"] = db.lessons(limit=200)
    return jsonify(full)


HTML = r"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Strategy Tracker</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0B1220;--panel:#121B2E;--line:#1E2A44;--ink:#D7E0EF;--dim:#8593AC;
--up:#3FB68B;--dn:#E0556B;--live:#F5A623;--dead:#6b7280;--mono:'IBM Plex Mono',ui-monospace,monospace}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.45 Inter,system-ui,sans-serif;padding:14px;max-width:1280px;margin:auto}
h1{font:600 16px var(--mono);letter-spacing:.04em}
.sub{color:var(--dim);font-size:12px;margin:4px 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;display:flex;flex-direction:column}
.ph{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:6px}
.ph h2{font:600 13px var(--mono);text-transform:uppercase;letter-spacing:.04em;line-height:1.3}
.tag{font-size:10px;padding:2px 7px;border-radius:4px;font-weight:600;white-space:nowrap}
.tag.live{background:#1d3a2e;color:var(--up)}
.tag.paper{background:#3a3320;color:var(--live)}
.tag.dead{background:#2a2f38;color:var(--dead)}
.method{font-size:12px;color:var(--dim);margin:6px 0 10px}
.kpis{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px}
.kpi{font:600 20px var(--mono);font-variant-numeric:tabular-nums}
.kpi small{display:block;font-size:10px;color:var(--dim);font-weight:400;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px}
.pos{color:var(--up)}.neg{color:var(--dn)}
.status{font-size:11px;color:var(--dim);margin-bottom:6px}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle}
canvas{max-height:120px;margin:4px 0}
.empty{color:var(--dim);font-size:12px;padding:14px 0;text-align:center}
.risk{font-size:11px;color:var(--dim);border-top:1px solid var(--line);padding-top:8px;margin-top:auto}
.paper{font-size:9px;background:#22304a;color:#9db4e0;padding:1px 5px;border-radius:3px;font-weight:600;letter-spacing:.04em}
.pnl{font:700 22px var(--mono);margin:8px 0 6px;font-variant-numeric:tabular-nums}
.pnl small{font:400 10px var(--mono);color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
.conf{font-size:11px;color:var(--dim);background:#0f1828;border-left:2px solid var(--up);padding:6px 9px;border-radius:0 6px 6px 0;margin:8px 0}
details{margin-top:8px}summary{font-size:11px;color:var(--live);cursor:pointer;font-family:var(--mono)}
table{width:100%;border-collapse:collapse;font:11px var(--mono);margin-top:6px}
th,td{text-align:right;padding:2px 4px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
.sec{font:600 13px var(--mono);text-transform:uppercase;letter-spacing:.06em;margin:22px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line);color:var(--ink)}
.sec small{font-weight:400;text-transform:none;letter-spacing:0;color:var(--dim);font-size:11px}
#sec_live{color:var(--up)}
.livedot{width:9px;height:9px;border-radius:50%;background:var(--up);display:inline-block;margin-right:8px;box-shadow:0 0 0 0 rgba(63,182,139,.7);animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(63,182,139,.6)}70%{box-shadow:0 0 0 7px rgba(63,182,139,0)}100%{box-shadow:0 0 0 0 rgba(63,182,139,0)}}
.panel.livecard{border-color:var(--up);box-shadow:0 0 0 1px rgba(63,182,139,.25)}
.real{font:600 12px var(--mono);background:#0f2018;border-left:2px solid var(--up);padding:7px 10px;border-radius:0 6px 6px 0;margin:8px 0;color:var(--ink)}
.real .lbl{color:var(--up);letter-spacing:.04em}
</style>
<h1>STRATEGY TRACKER</h1>
<div class=sub id=sub>loading…</div>
<h2 class=sec id=sec_live><span class="livedot"></span>LIVE — real broker orders <small>· actual filled demo trades (Alpaca paper), most → least profitable</small></h2>
<div class=grid id=grid_live></div>
<h2 class=sec id=sec_paper>PAPER — simulation <small>· resolved from real bars, no orders placed · most → least profitable</small></h2>
<div class=grid id=grid_paper></div>
<script>
const pct=x=>x==null?'—':(100*x).toFixed(1)+'%';
const f=(x,d=2)=>x==null?'—':Number(x).toFixed(d);
const tcls=s=>/LIVE/.test(s)?'live':/DEAD/.test(s)?'dead':'paper';
const charts={};
function fmtT(ts){const d=new Date(ts*1000);return d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});}
function rowsTable(c){
 if(!c.recent||!c.recent.length)return '';
 const bps = c.unit==='bps';
 const head = bps
   ? '<tr><th>time</th><th>sym</th><th>side</th><th>outcome</th><th>bps</th></tr>'
   : '<tr><th>time</th><th>sym</th><th>side</th><th>entry</th><th>res</th><th>pnl</th></tr>';
 const body = c.recent.map(r=>bps
   ? `<tr><td>${fmtT(r.t)}</td><td>${r.symbol}</td><td>${r.side}</td><td>${r.outcome}</td><td class="${r.ret_bps>=0?'pos':'neg'}">${r.ret_bps>0?'+':''}${r.ret_bps}</td></tr>`
   : `<tr><td>${fmtT(r.t)}</td><td>${r.symbol}</td><td>${r.side}</td><td>${f(r.entry,2)}</td><td>${r.outcome}</td><td class="${r.pnl>=0?'pos':'neg'}">${f(r.pnl,2)}</td></tr>`
 ).join('');
 return `<details><summary>recent trades (${c.recent.length})</summary><table>${head}${body}</table></details>`;
}
function kpis(c){
 if(!c.n) return '';
 if(c.unit==='$'){ // meanrev / polymarket
   return `<div class=kpis>
     <div class=kpi><small>Resolved</small>${c.n}</div>
     <div class="kpi ${c.hit>=c.avg_entry?'pos':'neg'}"><small>Hit</small>${pct(c.hit)}</div>
     <div class=kpi><small>Avg entry</small>${f(c.avg_entry,2)}</div>
     <div class="kpi ${c.ev>0?'pos':'neg'}"><small>EV/bet</small>${pct(c.ev)}</div></div>`;
 }
 return `<div class=kpis>
   <div class=kpi><small>Resolved</small>${c.n}</div>
   <div class=kpi><small>Hit/touch</small>${pct(c.hit)}</div>
   <div class=kpi><small>Gross bps</small>${c.exp_bps>0?'+':''}${c.exp_bps}</div>
   <div class="kpi ${c.net_bps>0?'pos':'neg'}"><small>Net bps</small>${c.net_bps>0?'+':''}${c.net_bps}</div></div>`;
}
function pnlLine(c){
 if(!c.n) return '';
 const v=c.pnl_total, pos=v>=0;
 const txt = c.unit==='$' ? ((pos?'+$':'−$')+Math.abs(v).toFixed(2))
                          : ((pos?'+':'')+v+' bps');
 return `<div class=pnl><span class="${pos?'pos':'neg'}">${txt}</span>`
        +` <small>total P&amp;L · paper${c.unit==='bps'?' · net of costs':''}</small></div>`;
}
function confLine(c){ return c.confidence?`<div class=conf>✓ ${c.confidence}</div>`:''; }
function venuesLine(c){
 if(!c.venues||!Object.keys(c.venues).length) return '';
 const parts=Object.entries(c.venues).map(([v,x])=>
   `<b style="color:var(--ink)">${v}</b> ${pct(x.hit)} <span class="${x.net_bps>0?'pos':'neg'}">${x.net_bps>0?'+':''}${x.net_bps}bps</span> (${x.n})`);
 return `<div class=status>real venues: ${parts.join(' · ')}</div>`;
}
function realLine(c){
 if(!c.real) return '';
 const x=c.real, p=x.net_bps>=0;
 return `<div class=real><span class=lbl>● REAL ${(x.venue||'').toUpperCase()} FILLS</span> — ${x.n} round-trip${x.n==1?'':'s'} ·
   hit ${pct(x.hit)} · <span class="${p?'pos':'neg'}">${p?'+':''}${x.net_bps} bps/trade</span> ·
   P&amp;L <span class="${x.pnl>=0?'pos':'neg'}">${x.pnl>=0?'+':''}${x.pnl} bps</span></div>`;
}
function costnote(c){
 const sp = c.spread_bps==null ? 'measuring…'
   : (c.spread_bps+' bps live ('+c.spread_n+' trades)');
 return `<div class=status style="color:var(--dim)">net = gross − spread ${sp} − fee ${c.fee_bps} bps</div>`;
}
function baseline(c){
 if(c.rw_base==null) return '';
 const real = c.edge_pp>0;
 return `<div class=status>hit ${pct(c.hit)} vs random-walk ${pct(c.rw_base)}
   · <span class="${real?'pos':'neg'}">edge ${c.edge_pp>0?'+':''}${c.edge_pp}pp</span>
   <span style="color:var(--dim)">(target=intrabar touch, stop=close)</span></div>`;
}
async function tick(){
 const s=await(await fetch('/api/stats')).json();
 document.getElementById('sub').textContent='updated '+new Date(s.last_update*1000).toLocaleTimeString();
 const gl=document.getElementById('grid_live'), gp=document.getElementById('grid_paper');
 let nlive=0;
 for(const [name,c] of Object.entries(s.strategies)){
  const live=c.group==='live'; if(live) nlive++;
  let p=document.getElementById('p_'+name);
  if(!p){p=document.createElement('div');p.id='p_'+name;}
  p.className='panel'+(live?' livecard':'');
  const alive=c.last_age_min!=null&&c.last_age_min<180;
  const armed=c.pending>0;
  p.innerHTML=`
   <div class=ph><h2>${c.label}</h2><span class="tag ${tcls(c.status)}">${c.status}</span></div>
   <div class=status><span class=dot style="background:${alive?'var(--live)':'var(--dim)'}"></span>
     ${c.last_age_min!=null?('last signal '+f(c.last_age_min,0)+'m ago'):'no signals yet'}
     · ${c.pending} pending${armed?' · armed':''} · ${c.symbols}</div>
   <div class=method><b style="color:var(--ink)">${c.platform||c.venue||''}</b>
     <span class=paper>${(c.mode||'paper').toUpperCase()}</span>
     · ${c.domain||''} · ${c.kind||''}</div>
   ${realLine(c)}
   ${c.n?pnlLine(c)+kpis(c)+baseline(c)+(c.unit==='bps'?costnote(c):'')+venuesLine(c)+'<canvas id="cv_'+name+'"></canvas>'+confLine(c)+rowsTable(c):'<div class=empty>No resolved trades yet.</div>'}
   <div class=risk>🔒 method, parameters &amp; research are private — <a href="/admin" style="color:var(--live)">admin</a> for the full record.</div>`;
  (live?gl:gp).appendChild(p);    // route + keep profit order (API order)
  if(c.n&&c.equity){
   const ctx=document.getElementById('cv_'+name);
   const col=c.equity[c.equity.length-1]>=0?'#3FB68B':'#E0556B';
   if(charts[name]){charts[name].data.labels=c.equity.map((_,i)=>i+1);
     charts[name].data.datasets[0].data=c.equity;
     charts[name].data.datasets[0].borderColor=col;charts[name].update('none');}
   else charts[name]=new Chart(ctx,{type:'line',
     data:{labels:c.equity.map((_,i)=>i+1),
       datasets:[{data:c.equity,borderColor:col,pointRadius:0,tension:.2,
         label:'cum '+(c.unit==='$'?'P&L $':'bps')}]},
     options:{plugins:{legend:{display:false},
       title:{display:true,text:'cumulative '+(c.unit==='$'?'P&L $':'bps'),color:'#8593AC',font:{size:10}}},
       scales:{x:{display:false},y:{grid:{color:'#1E2A44'}}}}});
  }
 }
 document.getElementById('sec_live').style.display=nlive?'':'none';
 document.getElementById('grid_live').style.display=nlive?'':'none';
}
tick();setInterval(tick,30000);
</script>"""


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


ADMIN_HTML = r"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Strategy Lab — Admin</title>
<style>
body{background:#0B1220;color:#D7E0EF;font:14px/1.5 ui-monospace,monospace;padding:18px;max-width:1100px;margin:auto}
h1{font-size:16px}h2{font-size:14px;color:#F5A623;border-bottom:1px solid #1E2A44;padding-bottom:4px;margin-top:26px}
.s{background:#121B2E;border:1px solid #1E2A44;border-radius:8px;padding:14px;margin:12px 0}
.k{color:#8593AC}.v{color:#D7E0EF}.pos{color:#3FB68B}.neg{color:#E0556B}
pre{white-space:pre-wrap;word-break:break-word;background:#0B1220;border:1px solid #1E2A44;padding:8px;border-radius:6px;font-size:12px}
a{color:#F5A623}.row{margin:3px 0}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{text-align:left;padding:2px 6px;border-bottom:1px solid #1E2A44}
</style>
<h1>STRATEGY LAB — ADMIN <span class=k id=sub></span></h1>
<div id=app>authenticating…</div>
<script>
const pct=x=>x==null?'—':(100*x).toFixed(1)+'%';
async function load(){
 const r=await fetch('/api/admin/stats');
 if(r.status===401){document.getElementById('app').innerHTML='Login cancelled. <a href="/admin">retry</a>';return;}
 if(r.status===503){document.getElementById('app').innerHTML='Admin not configured (set ADMIN_PASSWORD).';return;}
 const s=await r.json();
 document.getElementById('sub').textContent='· updated '+new Date(s.last_update*1000).toLocaleString();
 let h='';
 for(const [n,c] of Object.entries(s.strategies)){
  const p=c.provenance||{};
  h+=`<div class=s><h2>${c.label} <span class=k>[${c.lifecycle}${c.role?'/'+c.role:''}]</span></h2>
   <div class=row><span class=k>status</span> ${c.status} · ${c.domain} · ${c.kind} · ${c.venue} · exec=${c.exec_model}</div>
   <div class=row><span class=k>results</span> n=${c.n||0} hit=${pct(c.hit)} ${c.unit==='$'?('EV/bet '+pct(c.ev)):('net '+(c.net_bps??'—')+'bps')}</div>
   <div class=row><span class=k>method (how it's used)</span> ${c.method||'—'}</div>
   <div class=row><span class=k>risk</span> ${c.risk||'—'}</div>
   <div class=row><span class=k>how it was found</span> ${p.hypothesis||'—'} <span class=k>(${(p.created_by||'?')}, ${(p.date||'?')})</span></div>
   <div class=row><span class=k>research refs</span> ${(p.research_refs||[]).join(', ')||'—'}</div>
   <div class=row><span class=k>signal</span></div><pre>${JSON.stringify(c.signal,null,1)}</pre>
   <div class=row><span class=k>gate</span> ${JSON.stringify(c.gate)}</div>
   ${(c.experiments&&c.experiments.length)?'<div class=row><span class=k>experiments</span></div>'+expTable(c.experiments):''}
   ${(c.versions&&c.versions.length)?'<div class=row><span class=k>param history</span> '+c.versions.length+' change(s)</div>':''}
  </div>`;
 }
 h+='<h2>LESSONS — do not re-litigate</h2>'+(s.lessons&&s.lessons.length?lessonsTable(s.lessons):'<div class=k>none yet</div>');
 document.getElementById('app').innerHTML=h;
}
function expTable(e){return '<table><tr><th>date</th><th>kind</th><th>verdict</th></tr>'+
  e.map(x=>`<tr><td>${new Date(x.ts*1000).toLocaleDateString()}</td><td>${x.kind}</td><td>${x.verdict||''}</td></tr>`).join('')+'</table>';}
function lessonsTable(l){return '<table><tr><th>date</th><th>verdict</th><th>idea</th><th>revisit if</th></tr>'+
  l.map(x=>`<tr><td>${new Date(x.ts*1000).toLocaleDateString()}</td><td>${x.verdict}</td><td>${x.idea}</td><td>${x.redo_bar||''}</td></tr>`).join('')+'</table>';}
load();
</script>"""


@app.route("/admin")
def admin_page():
    return Response(ADMIN_HTML, mimetype="text/html")


@app.route("/docs")
def docs_page():
    """The living infrastructure document, served from disk so edits show live.
    Maintained by the daily agent — see the maintenance contract inside it."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "docs", "INFRA.html")
    if not os.path.exists(path):
        return Response("INFRA.html missing", 404)
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8050")))
