"""
dashboard_db.py — DB-backed monitor for both strategies. Reads tracker.db.

Two strategy panels:
  meanrev (Polymarket) — hit rate, EV/bet, calibration (entry price vs win),
                         the live verdict for the binary edge.
  gaptrav (forex paper) — touch rate, expectancy bps, RR — the paper experiment.

Run: python3 dashboard_db.py   ->  http://localhost:8050
"""
import os
import time
import numpy as np
from flask import Flask, jsonify, Response
import db

SIZE_USD = float(os.environ.get("SIZE_USD", "100"))
app = Flask(__name__)
db.init()   # ensure tables exist on web-service boot


@app.route("/api/stats")
def stats():
    db.init()
    bets, trades = db.stats()
    now = int(time.time())
    out = {"meanrev": {}, "gaptrav": {}, "last_update": now}

    mr = [b for b in bets if b["strategy"] == "meanrev"]
    if mr:
        won = np.array([b["won"] for b in mr])
        ent = np.array([b["entry_price"] for b in mr])
        pnl = np.array([b["pnl"] for b in mr])
        ts = np.array([b["ts"] for b in mr])
        order = np.argsort(ts)
        cal = {}
        for b in mr:
            k = round(b["entry_price"] * 20) / 20
            cal.setdefault(k, []).append(b["won"])
        out["meanrev"] = dict(
            n=len(mr), hit=round(float(won.mean()), 4),
            avg_entry=round(float(ent.mean()), 3),
            ev=round(float(pnl.mean()) / SIZE_USD, 4),
            equity=np.round(np.cumsum(pnl[order]), 2).tolist(),
            cal=[{"p": round(float(k), 2), "win": round(float(np.mean(v)), 3),
                  "n": len(v)} for k, v in sorted(cal.items()) if len(v) >= 5],
            last_age_min=round((now - int(ts.max())) / 60, 1))

    gt = [t for t in trades if t["strategy"] == "gaptrav"]
    if gt:
        won = np.array([t["won"] for t in gt])
        ret = np.array([t["ret_bps"] for t in gt])
        ts = np.array([t["ts"] for t in gt])
        order = np.argsort(ts)
        out["gaptrav"] = dict(
            n=len(gt), touch_rate=round(float(won.mean()), 4),
            exp_bps=round(float(ret.mean()), 1),
            equity_bps=np.round(np.cumsum(ret[order]), 1).tolist(),
            last_age_min=round((now - int(ts.max())) / 60, 1))
    return jsonify(out)


HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Strategy Tracker</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0B1220;--panel:#121B2E;--line:#1E2A44;--ink:#D7E0EF;--dim:#8593AC;--up:#3FB68B;--dn:#E0556B;--live:#F5A623;--mono:'IBM Plex Mono',ui-monospace,monospace}
*{box-sizing:border-box;margin:0}body{background:var(--bg);color:var(--ink);font:15px/1.45 Inter,system-ui,sans-serif;padding:14px;max-width:1100px;margin:auto}
h1{font:600 16px var(--mono);letter-spacing:.04em;margin-bottom:4px}
.sub{color:var(--dim);font-size:12px;margin-bottom:16px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:760px){.row{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
.ph{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px}
.ph h2{font:600 13px var(--mono);text-transform:uppercase;letter-spacing:.06em}
.tag{font-size:10px;padding:2px 7px;border-radius:4px;font-weight:600}
.tag.live{background:#1d3a2e;color:var(--up)}.tag.paper{background:#3a3320;color:var(--live)}
.kpis{display:flex;gap:18px;margin-bottom:14px;flex-wrap:wrap}
.kpi{font:600 22px var(--mono);font-variant-numeric:tabular-nums}
.kpi small{display:block;font-size:11px;color:var(--dim);font-weight:400;text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px}
.pos{color:var(--up)}.neg{color:var(--dn)}
canvas{max-height:180px;margin-top:6px}
.empty{color:var(--dim);font-size:13px;padding:24px 0;text-align:center}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px}
</style>
<h1>STRATEGY TRACKER</h1>
<div class=sub id=sub>loading…</div>
<div class=row>
 <div class=panel><div class=ph><h2>Mean-reversion · Polymarket</h2><span class="tag live">LIVE</span></div><div id=mr></div></div>
 <div class=panel><div class=ph><h2>Gap-traversal · Forex</h2><span class="tag paper">PAPER</span></div><div id=gt></div></div>
</div>
<script>
const pct=x=>x==null?'—':(100*x).toFixed(1)+'%';
const f=(x,d=2)=>x==null?'—':Number(x).toFixed(d);
let mrEq,mrCal,gtEq;
async function tick(){
 const s=await(await fetch('/api/stats')).json();
 document.getElementById('sub').textContent='updated '+new Date(s.last_update*1000).toLocaleTimeString();
 const m=s.meanrev;
 if(!m||!m.n){document.getElementById('mr').innerHTML='<div class=empty>No Polymarket bets logged yet.<br>Start: python3 runner.py</div>';}
 else{
  const alive=m.last_age_min<120;
  document.getElementById('mr').innerHTML=`
   <div class=kpis>
    <div class=kpi><small>Resolved</small>${m.n}</div>
    <div class="kpi ${m.hit>=m.avg_entry?'pos':'neg'}"><small>Hit rate</small>${pct(m.hit)}</div>
    <div class=kpi><small>Avg entry</small>${f(m.avg_entry,2)}</div>
    <div class="kpi ${m.ev>0?'pos':'neg'}"><small>EV/bet</small>${pct(m.ev)}</div>
   </div>
   <div style="font-size:11px;color:var(--dim)"><span class=dot style="background:${alive?'var(--live)':'var(--dn)'}"></span>last ${f(m.last_age_min,0)}m ago · hit must exceed entry price</div>
   <canvas id=mrcal></canvas><canvas id=mreq></canvas>`;
  const cd=m.cal||[];
  const cdata={datasets:[
   {label:'fair',type:'line',data:[{x:.45,y:.45},{x:.65,y:.65}],borderColor:'#8593AC',borderDash:[5,4],pointRadius:0},
   {type:'bubble',data:cd.map(c=>({x:c.p,y:c.win,r:Math.min(4+Math.sqrt(c.n),12)})),backgroundColor:'#F5A623'}]};
  if(!mrCal)mrCal=new Chart(mrcal,{data:cdata,options:{plugins:{legend:{display:false},title:{display:true,text:'calibration: entry price vs win rate',color:'#8593AC',font:{size:11}}},scales:{x:{min:.4,max:.7,grid:{color:'#1E2A44'}},y:{min:.3,max:.8,grid:{color:'#1E2A44'}}}}});
  else{mrCal.data=cdata;mrCal.update('none');}
  if(!mrEq)mrEq=new Chart(mreq,{type:'line',data:{labels:m.equity.map((_,i)=>i+1),datasets:[{label:'cum P&L $',data:m.equity,borderColor:'#3FB68B',pointRadius:0,tension:.2}]},options:{plugins:{legend:{display:false}},scales:{x:{display:false},y:{grid:{color:'#1E2A44'}}}}});
  else{mrEq.data.labels=m.equity.map((_,i)=>i+1);mrEq.data.datasets[0].data=m.equity;mrEq.update('none');}
 }
 const g=s.gaptrav;
 if(!g||!g.n){document.getElementById('gt').innerHTML='<div class=empty>No forex paper trades yet.</div>';}
 else{
  document.getElementById('gt').innerHTML=`
   <div class=kpis>
    <div class=kpi><small>Resolved</small>${g.n}</div>
    <div class=kpi><small>Touch rate</small>${pct(g.touch_rate)}</div>
    <div class="kpi ${g.exp_bps>0?'pos':'neg'}"><small>Exp bps</small>${g.exp_bps>0?'+':''}${g.exp_bps}</div>
   </div>
   <div style="font-size:11px;color:var(--dim)">paper experiment — watching if live diverges from ~0 backtest expectancy</div>
   <canvas id=gteq></canvas>`;
  if(!gtEq)gtEq=new Chart(gteq,{type:'line',data:{labels:g.equity_bps.map((_,i)=>i+1),datasets:[{label:'cum bps',data:g.equity_bps,borderColor:'#F5A623',pointRadius:0,tension:.2}]},options:{plugins:{legend:{display:false},title:{display:true,text:'cumulative bps',color:'#8593AC',font:{size:11}}},scales:{x:{display:false},y:{grid:{color:'#1E2A44'}}}}});
  else{gtEq.data.labels=g.equity_bps.map((_,i)=>i+1);gtEq.data.datasets[0].data=g.equity_bps;gtEq.update('none');}
 }
}
tick();setInterval(tick,30000);
</script>"""


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8050")))
