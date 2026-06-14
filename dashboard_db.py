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
import os
import time

import numpy as np
from flask import Flask, jsonify, Response

import db
import strategies as S

SIZE_USD = float(os.environ.get("SIZE_USD", "100"))
app = Flask(__name__)
db.init()


def _age_min(ts, now):
    return round((now - int(ts)) / 60, 1) if ts else None


@app.route("/api/stats")
def stats():
    db.init()
    now = int(time.time())
    bets = db.recent_bets(800)
    trades = db.recent_trades(2000)
    act = db.activity()

    out = {"last_update": now, "strategies": {}}
    for name, meta in S.STRATEGIES.items():
        a = act.get(name, {})
        card = dict(label=meta["label"], status=meta["status"], kind=meta["kind"],
                    venue=meta["venue"], symbols=meta["symbols"],
                    method=meta["method"], risk=meta["risk"],
                    pending=a.get("pending", 0),
                    last_age_min=_age_min(a.get("last_ts"), now),
                    n=0, recent=[])

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
        else:                                        # everything else -> trades
            rows = [t for t in trades if t["strategy"] == name]
            res = [t for t in rows if t["outcome"]]
            if res:
                res_sorted = sorted(res, key=lambda t: t["ts"])
                won = np.array([t["won"] for t in res], float)
                ret = np.array([t["ret_bps"] for t in res_sorted], float)
                card.update(
                    n=len(res), hit=round(float(won.mean()), 4),
                    exp_bps=round(float(ret.mean()), 1),
                    equity=np.round(np.cumsum(ret), 1).tolist(), unit="bps",
                    recent=[dict(t=t["ts"], symbol=t["symbol"], side=t["side"],
                                 entry=t["entry"], exit=t["exit"],
                                 outcome=t["outcome"], ret_bps=t["ret_bps"])
                            for t in sorted(res, key=lambda t: t["ts"],
                                            reverse=True)[:25]])
        out["strategies"][name] = card
    return jsonify(out)


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
details{margin-top:8px}summary{font-size:11px;color:var(--live);cursor:pointer;font-family:var(--mono)}
table{width:100%;border-collapse:collapse;font:11px var(--mono);margin-top:6px}
th,td{text-align:right;padding:2px 4px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
</style>
<h1>STRATEGY TRACKER</h1>
<div class=sub id=sub>loading…</div>
<div class=grid id=grid></div>
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
   <div class="kpi ${c.exp_bps>0?'pos':'neg'}"><small>Exp bps</small>${c.exp_bps>0?'+':''}${c.exp_bps}</div></div>`;
}
async function tick(){
 const s=await(await fetch('/api/stats')).json();
 document.getElementById('sub').textContent='updated '+new Date(s.last_update*1000).toLocaleTimeString();
 const grid=document.getElementById('grid');
 for(const [name,c] of Object.entries(s.strategies)){
  let p=document.getElementById('p_'+name);
  if(!p){p=document.createElement('div');p.className='panel';p.id='p_'+name;grid.appendChild(p);}
  const alive=c.last_age_min!=null&&c.last_age_min<180;
  const armed=c.pending>0;
  p.innerHTML=`
   <div class=ph><h2>${c.label}</h2><span class="tag ${tcls(c.status)}">${c.status}</span></div>
   <div class=status><span class=dot style="background:${alive?'var(--live)':'var(--dim)'}"></span>
     ${c.last_age_min!=null?('last signal '+f(c.last_age_min,0)+'m ago'):'no signals yet'}
     · ${c.pending} pending${armed?' · armed':''} · ${c.symbols}</div>
   <div class=method>${c.method}</div>
   ${c.n?kpis(c)+'<canvas id="cv_'+name+'"></canvas>'+rowsTable(c):'<div class=empty>No resolved trades yet.</div>'}
   <div class=risk>${c.risk}</div>`;
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
}
tick();setInterval(tick,30000);
</script>"""


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8050")))
