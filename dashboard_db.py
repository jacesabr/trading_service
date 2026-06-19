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
    "group", "real", "broker", "since_ts", "avg_rr", "n_open",
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


app = Flask(__name__)
app.json.sort_keys = False        # preserve our most->least profitable ordering
db.init()


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
    execs = db.recent_executions(2000)            # REAL broker fills only (sim purged)
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

        # REAL broker fills only — the lab is real-execution now (sim deleted).
        # order:=Alpaca crypto round-trip · bracket:=Alpaca equity OCO · byb*=Bybit
        # demo. ret_bps is the realized round-trip (real spread already in the fill).
        ex = [e for e in execs if e["strategy"] == name]
        real = [e for e in ex if (e.get("ref") or "").startswith(("order:", "bracket:", "byb"))]
        if real:
            res = [e for e in real if e.get("outcome") not in (None, "", "void")]
            ret = np.array([e["ret_bps"] or 0 for e in
                            sorted(res, key=lambda e: e["ts"])], float)
            won = np.array([1.0 if (e.get("won") or (e.get("ret_bps") or 0) > 0)
                            else 0.0 for e in res], float)
            rr_list = [abs(e["target"] - e["entry"]) / abs(e["entry"] - e["stop"])
                       for e in real if e.get("target") and e.get("stop")
                       and e.get("entry") and abs(e["entry"] - e["stop"]) > 0]
            vlist = [e["venue"] for e in real]
            broker_v = max(set(vlist), key=vlist.count)
            card.update(
                n=len(res), n_open=len(real) - len(res), unit="bps", mode="demo",
                broker=BROKER_NAME.get(broker_v, broker_v), venue=broker_v,
                hit=round(float(won.mean()), 4) if len(won) else None,
                net_bps=round(float(ret.mean()), 1) if len(ret) else None,
                pnl_total=round(float(ret.sum()), 1) if len(ret) else 0,
                avg_rr=round(float(np.mean(rr_list)), 2) if rr_list else None,
                since_ts=min(e["ts"] for e in real),
                last_age_min=_age_min(max(e["ts"] for e in real), now),
                equity=np.round(np.cumsum(ret), 1).tolist() if len(ret) else [],
                recent=[dict(t=e["ts"], symbol=e["symbol"], side=e["side"],
                             entry=e["entry"], exit=e.get("exit"),
                             outcome=e.get("outcome") or "open", ret_bps=e["ret_bps"])
                        for e in sorted(real, key=lambda e: e["ts"], reverse=True)[:25]])
            card["rank_score"] = card["pnl_total"] if len(ret) else -0.5  # most→least P&L
            card["group"] = "live"
            card["confidence"] = (f"Real {card['broker']} demo fills — broker-executed "
                                  f"(entry, exit & P&L from the venue), not local sim. "
                                  f"n={len(res)}" + (f", {len(real)-len(res)} open."
                                  if len(real) > len(res) else "."))
        else:
            # no real broker fills yet — show the strategy (collapsed) as awaiting
            # live execution. Sim is deleted, so it has no results to display.
            card.update(n=0, n_open=0, unit="bps", mode="demo",
                        broker="—", since_ts=None, rank_score=-1e12)
            card["group"] = "idle"
            card["confidence"] = ("No broker fills yet — this strategy isn't placing "
                                  "real demo orders, so there's nothing to show "
                                  "(local sim was removed).")
        out["strategies"][name] = card           # show ALL strategies
    # real-fill strategies first (most → least profitable), then idle ones
    items = sorted(out["strategies"].items(),
                   key=lambda kv: kv[1].get("rank_score", -1e12), reverse=True)
    out["strategies"] = dict(items)
    out["live_count"] = sum(1 for _, c in items if c.get("group") == "live")
    return out


@app.route("/api/stats")
def stats():
    """PUBLIC — results + identity only; the HOW is redacted."""
    full = _build_cards()
    full["strategies"] = {n: _public(c) for n, c in full["strategies"].items()}
    return jsonify(full)


BROKER_NAME = {"alpaca": "Alpaca paper", "binance_sim": "Binance (sim)",
               "oanda": "OANDA", "kraken": "Kraken paper",
               "bybit_demo": "Bybit demo", "binance_futures": "Binance futures demo"}


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
.idot{width:6px;height:6px;border-radius:50%;display:inline-block;margin-right:4px}
.b-long{color:var(--up);font-weight:600}.b-short{color:var(--dn);font-weight:600}.b-none{color:var(--dim)}
.st{font-size:9px;padding:1px 6px;border-radius:3px;font-weight:600;white-space:nowrap}
.st-extracted{background:#1d3a2e;color:var(--up)}
.st-needs_vision{background:#3a3320;color:var(--live)}
.st-dropped_tf{background:#2a2f38;color:var(--dead)}
.st-stored{background:#22304a;color:#9db4e0}
.st-open{background:#13314d;color:#5fa8e0}
.st-pending{background:#2d2740;color:#b39ddb}
.st-resolved{background:#1d3a2e;color:var(--up)}
.st-invalidated{background:#2a2f38;color:var(--dead)}
.st-no_venue{background:#2a2f38;color:var(--dead)}
.st-expired{background:#2a2f38;color:var(--dead)}
.thumb{width:42px;height:26px;object-fit:cover;border-radius:3px;border:1px solid var(--line);vertical-align:middle}
.bas{font-size:9px;color:var(--dim)}.bas-chart{color:var(--up)}.bas-generated{color:var(--live)}
a.idea-link{color:var(--live);text-decoration:none}a.idea-link:hover{text-decoration:underline}
/* stat cards + dropdowns */
.statgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:4px 0 14px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.stat .v{font:600 22px var(--mono);font-variant-numeric:tabular-nums}
.stat .l{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px}
details#det_ongoing,details#det_prev{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin-bottom:10px;padding:4px 0}
details#det_ongoing>summary,details#det_prev>summary{font:600 13px var(--mono);text-transform:uppercase;letter-spacing:.04em;color:var(--ink);cursor:pointer;padding:10px 14px;list-style:none}
details>summary::-webkit-details-marker{display:none}
details#det_ongoing>summary:before,details#det_prev>summary:before{content:'▸ ';color:var(--live)}
details[open]#det_ongoing>summary:before,details[open] >summary:before{content:'▾ '}
.cnt{color:var(--dim);font-weight:400}
.tw{overflow-x:auto;padding:0 6px 6px}
.broker{font-size:10px;padding:1px 6px;border-radius:3px;font-weight:600;white-space:nowrap}
.broker-alpaca{background:#13314d;color:#5fa8e0}.broker-sim{background:#2d2740;color:#b39ddb}
/* collapsible strategy cards */
details.scard{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
details.scard[open]{border-color:var(--up);box-shadow:0 0 0 1px rgba(63,182,139,.2)}
details.scard>summary{cursor:pointer;list-style:none;padding:12px 14px}
details.scard>summary::-webkit-details-marker{display:none}
details.scard>summary:before{content:'▸';color:var(--up);float:right;font:600 13px var(--mono)}
details.scard[open]>summary:before{content:'▾'}
details.scard .ph{display:flex;align-items:center;gap:8px;margin-bottom:10px}
details.scard .ph h2{font:600 14px var(--mono);color:var(--ink)}
.sbadge{font:600 9px var(--mono);text-transform:uppercase;letter-spacing:.05em;padding:2px 7px;border-radius:4px;white-space:nowrap}
.cardsum{display:grid;grid-template-columns:repeat(auto-fit,minmax(78px,1fr));gap:6px 12px}
.si{display:flex;flex-direction:column;gap:1px}
.si .sl{font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
.si .sv{font:600 14px var(--mono);font-variant-numeric:tabular-nums}
.carddetail{padding:0 14px 14px;border-top:1px solid var(--line);margin-top:2px}
.carddetail .method{margin:10px 0}
</style>
<h1>STRATEGY TRACKER</h1>
<div class=sub id=sub>loading…</div>


<h2 class=sec id=sec_live><span class="livedot"></span>STRATEGIES — real broker results <small>· click a card to expand · live (real fills) first, then strategies awaiting execution</small></h2>
<div class=grid id=grid_live></div>
<div class=empty id=no_strats style="display:none">No strategy has real broker fills yet — results appear once a live demo order resolves.</div>
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
function pnlStr(c){ const v=c.pnl_total; if(v==null) return '—';
  return c.unit==='$' ? ((v>=0?'+$':'−$')+Math.abs(v).toFixed(2)) : ((v>=0?'+':'')+v+' bps'); }
function runtimeStr(ts){ if(!ts) return '—'; let s=Math.max(0,Math.floor(Date.now()/1000-ts));
  const d=Math.floor(s/86400); s-=d*86400; const h=Math.floor(s/3600); const m=Math.floor((s-h*3600)/60);
  return d?`${d}d ${h}h`:(h?`${h}h ${m}m`:`${m}m`); }
function si(l,v,cls){ return `<div class=si><span class=sl>${l}</span><span class="sv ${cls||''}">${v}</span></div>`; }
function buildChart(name,c){ const ctx=document.getElementById('cv_'+name); if(!ctx||!c.equity||!c.equity.length) return;
  const col=c.equity[c.equity.length-1]>=0?'#3FB68B':'#E0556B';
  if(charts[name]){charts[name].data.labels=c.equity.map((_,i)=>i+1);charts[name].data.datasets[0].data=c.equity;
    charts[name].data.datasets[0].borderColor=col;charts[name].update('none');return;}
  charts[name]=new Chart(ctx,{type:'line',data:{labels:c.equity.map((_,i)=>i+1),
    datasets:[{data:c.equity,borderColor:col,pointRadius:0,tension:.2}]},
    options:{plugins:{legend:{display:false},title:{display:true,text:'cumulative bps',color:'#8593AC',font:{size:10}}},
      scales:{x:{display:false},y:{grid:{color:'#1E2A44'}}}}}); }
async function tick(){
 const s=await(await fetch('/api/stats')).json();
 document.getElementById('sub').textContent='updated '+new Date(s.last_update*1000).toLocaleTimeString();
 const gl=document.getElementById('grid_live'), names=Object.keys(s.strategies);
 document.getElementById('no_strats').style.display=names.length?'none':'';
 document.getElementById('sec_live').style.display=names.length?'':'';
 for(const [name,c] of Object.entries(s.strategies)){
  let p=document.getElementById('p_'+name);
  if(!p){p=document.createElement('div');p.id='p_'+name;gl.appendChild(p);}  // preserve order
  const has=(c.n||0)+(c.n_open||0)>0;
  const winc=c.hit==null?'':(c.hit>=0.5?'pos':'neg'), pc=(c.pnl_total||0)>=0?'pos':'neg';
  const tradesTxt=has?((c.n||0)+(c.n_open?` <span style="color:var(--dim)">(+${c.n_open})</span>`:'')):'0';
  p.innerHTML=`
   <details class=scard>
     <summary>
       <div class=ph><h2>${c.label}</h2>${c.group==='live'
         ?'<span class=sbadge style="background:#13311f;color:var(--up)">● LIVE</span>'
         :'<span class=sbadge style="background:#1b2336;color:var(--dim)">no fills yet</span>'}</div>
       <div class=cardsum>
         ${si('P&amp;L', has?pnlStr(c):'—', has?pc:'')}
         ${si('Win / Loss', c.hit==null?'—':pct(c.hit), winc)}
         ${si('Trades', tradesTxt)}
         ${si('Avg R:R', c.avg_rr==null?'—':c.avg_rr)}
         ${si('Broker', c.broker||'—')}
         ${si('Running', runtimeStr(c.since_ts))}
       </div>
     </summary>
     <div class=carddetail>
       <div class=method><b style="color:var(--ink)">${c.broker||c.venue||''}</b>
         <span class=paper>${(c.mode||'demo').toUpperCase()}</span> · ${c.domain||''} · ${c.kind||''}
         ${c.symbols?'· '+c.symbols:''}</div>
       ${c.n?('<canvas id="cv_'+name+'"></canvas>'+rowsTable(c))
            :('<div class=empty>'+(c.n_open?c.n_open+' open · none resolved yet.':'No resolved trades yet.')+'</div>')}
       ${c.confidence?`<div class=conf>✓ ${c.confidence}</div>`:''}
       <div class=risk>🔒 method, parameters &amp; research are private — <a href="/admin" style="color:var(--live)">admin</a> for the full record.</div>
     </div>
   </details>`;
  const det=p.querySelector('details');
  det.addEventListener('toggle',()=>{ if(det.open) buildChart(name,c); });
 }
}
function ideaDir(d){return d===1?'<span class=b-long>LONG</span>':d===-1?'<span class=b-short>SHORT</span>':'<span class=b-none>—</span>';}
function px(v){return (v==null||v===0)?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:2});}
function fmtDT(ts){ if(!ts) return '<span style="color:var(--dim)">resting</span>';
  const d=new Date(ts*1000); return d.toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); }
function thumbCell(r){ return r.chart_image_url
  ? `<a href="${r.url}" target=_blank rel=noopener><img class=thumb src="${r.chart_image_url}" loading=lazy onerror="this.style.display='none'"></a>` : '—'; }
function brokerCell(v){ if(!v) return '—';
  const cls = v.indexOf('Alpaca')>=0?'broker-alpaca':'broker-sim';
  return `<span class="broker ${cls}">${v}</span>`; }
function usd(v){ if(v==null) return '—'; const s=v>=0?'+$':'−$'; return s+Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:2}); }
function rr(r){ if(!r.entry||!r.target||!r.stop) return '—';
  const risk=Math.abs(r.entry-r.stop); if(!risk) return '—';
  return (Math.abs(r.target-r.entry)/risk).toFixed(2); }
function statCard(l,v,cls){ return `<div class=stat><div class=l>${l}</div><div class="v ${cls||''}">${v}</div></div>`; }

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


def _serve_doc(filename, missing):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs",
                        filename)
    if not os.path.exists(path):
        return Response(missing, 404)
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@app.route("/docs")
def docs_page():
    """The living infrastructure document, served from disk so edits show live.
    Maintained by the daily agent — see the maintenance contract inside it."""
    return _serve_doc("INFRA.html", "INFRA.html missing")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8050")))
