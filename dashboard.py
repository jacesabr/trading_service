"""
dashboard.py — Live monitoring front end for the Polymarket paper trader.

Single-file Flask app. Reads paper_trades.csv (written by paper_trader.py)
and serves a dashboard answering two questions at a glance:
  1. Is the system alive? (status strip: last signal age, pending windows)
  2. Is the edge real? (calibration panel: entry price vs realized win rate —
     points above the fair-value diagonal mean the market underprices you)

Run:  python3 dashboard.py            ->  http://localhost:8050
"""
import glob
import os
import time
import numpy as np
import pandas as pd
from flask import Flask, jsonify, Response

LOG = "paper_trades.csv"
SIZE_USD = 100.0
BACKTEST_HIT = {"overbought_strong": 0.56, "overbought_rsi14_bbz": 0.56,
                "oversold_bounce": 0.53}

app = Flask(__name__)


def load():
    if not os.path.exists(LOG):
        return pd.DataFrame()
    df = pd.read_csv(LOG)
    return df


@app.route("/api/stats")
def stats():
    df = load()
    if df.empty:
        return jsonify(dict(empty=True))
    res = df[df["outcome"].notna() & (df["outcome"] != "")].copy()
    last_ts = int(df["ts"].max())
    out = dict(empty=False,
               total_signals=len(df),
               resolved=len(res),
               last_signal_age_min=round((time.time() - last_ts) / 60, 1),
               pending=int(len(df) - len(res)))
    if len(res):
        res["won"] = res["won"].astype(int)
        out["hit_rate"] = round(float(res["won"].mean()), 4)
        out["ev_taker"] = round(float(res["pnl_taker"].mean()) / SIZE_USD, 4)
        out["ev_maker"] = round(float(res["pnl_maker_if_filled"].mean()) / SIZE_USD, 4)
        out["avg_entry"] = round(float(res["taker_fill"].mean()), 3)
        res = res.sort_values("ts")
        out["equity_taker"] = np.round(res["pnl_taker"].cumsum().to_numpy(), 2).tolist()
        out["equity_maker"] = np.round(res["pnl_maker_if_filled"].cumsum()
                                       .to_numpy(), 2).tolist()
        out["equity_ts"] = res["ts"].tolist()
        # calibration: bucket entry price (2c), realized win rate
        res["bucket"] = (res["taker_fill"] * 50).round() / 50
        cal = (res.groupby("bucket")
               .agg(n=("won", "size"), win=("won", "mean")).reset_index())
        cal = cal[cal["n"] >= 5]
        out["cal"] = [dict(p=round(float(r.bucket), 2),
                           win=round(float(r.win), 3), n=int(r.n))
                      for r in cal.itertuples()]
        per_rule = (res.groupby("rule")
                    .agg(n=("won", "size"), win=("won", "mean"),
                         ev=("pnl_taker", "mean")).reset_index())
        out["rules"] = [dict(rule=r.rule, n=int(r.n),
                             win=round(float(r.win), 3),
                             ev=round(float(r.ev) / SIZE_USD, 4),
                             bt=BACKTEST_HIT.get(r.rule))
                        for r in per_rule.itertuples()]
        rec = res.tail(15).iloc[::-1]
        out["recent"] = [dict(t=time.strftime("%d %b %H:%M",
                                              time.localtime(int(r.ts))),
                              coin=r.coin, side=r.side, rule=r.rule,
                              fill=float(r.taker_fill), out=r.outcome,
                              pnl=float(r.pnl_taker))
                         for r in rec.itertuples()]
        days = max((res["ts"].max() - res["ts"].min()) / 86400, 1 / 24)
        out["signals_per_day"] = round(len(res) / days, 1)
    return jsonify(out)


HTML = """<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Edge Monitor — Polymarket 5m</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0B1220;--panel:#121B2E;--line:#1E2A44;--ink:#D7E0EF;--dim:#8593AC;
--up:#3FB68B;--dn:#E0556B;--live:#F5A623;--mono:'IBM Plex Mono',ui-monospace,Menlo,monospace}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.45 Inter,system-ui,sans-serif;padding:14px;max-width:1100px;margin:auto}
h1{font:600 17px var(--mono);letter-spacing:.04em}
.strip{display:flex;gap:14px;align-items:center;padding:10px 0 16px;flex-wrap:wrap}
.dot{width:9px;height:9px;border-radius:50%;background:var(--live);animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.3}}
@media (prefers-reduced-motion: reduce){.dot{animation:none}}
.dot.dead{background:var(--dn);animation:none}
.muted{color:var(--dim);font-size:13px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.kpi{font:600 26px var(--mono);font-variant-numeric:tabular-nums}
.kpi small{font-size:13px;color:var(--dim);font-weight:400}
.lbl{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}
.pos{color:var(--up)}.neg{color:var(--dn)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;margin-top:12px}
.panel h2{font:600 13px var(--mono);color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px}
table{width:100%;border-collapse:collapse;font:13px var(--mono);font-variant-numeric:tabular-nums}
td,th{padding:5px 8px;text-align:right;border-bottom:1px solid var(--line)}
td:first-child,th:first-child{text-align:left}
.empty{padding:40px;text-align:center;color:var(--dim)}
canvas{max-height:280px}
</style></head><body>
<div class="strip"><span class="dot" id="dot"></span><h1>EDGE MONITOR · POLYMARKET 5M</h1>
<span class="muted" id="status">connecting…</span></div>
<div id="app"></div>
<script>
const fmt=(x,d=2)=>x==null?'—':Number(x).toFixed(d);
const pct=x=>x==null?'—':(100*x).toFixed(1)+'%';
let eq,calc;
async function tick(){
 const r=await fetch('/api/stats');const s=await r.json();
 const app=document.getElementById('app');
 if(s.empty){app.innerHTML='<div class="panel empty">No trades logged yet.<br>Start the logger: <b>python3 paper_trader.py</b> — signals appear here as windows resolve.</div>';return}
 const alive=s.last_signal_age_min<120;
 document.getElementById('dot').className='dot'+(alive?'':' dead');
 document.getElementById('status').textContent=
   `last signal ${fmt(s.last_signal_age_min,0)}m ago · ${s.pending} pending · ${fmt(s.signals_per_day,1)} signals/day`;
 if(!document.getElementById('kpis')){
  app.innerHTML=`<div class="grid" id="kpis"></div>
  <div class="panel"><h2>Calibration — entry price vs realized win rate (above the line = market underprices you)</h2><canvas id="cal"></canvas></div>
  <div class="panel"><h2>Cumulative P&L per execution mode ($${'{'}100/bet)</h2><canvas id="eq"></canvas></div>
  <div class="panel"><h2>Per rule</h2><div id="rules"></div></div>
  <div class="panel"><h2>Recent resolutions</h2><div id="recent"></div></div>
  <div class="panel"><h2>Daily analyst report <span class="muted" id="rdate"></span></h2><pre id="report" style="white-space:pre-wrap;font:12px var(--mono);color:var(--dim);max-height:340px;overflow:auto"></pre></div>
  <div class="panel"><h2>Ask the assistant (live stats are attached automatically)</h2>
   <div style="display:flex;gap:8px"><input id="q" style="flex:1;background:var(--bg);border:1px solid var(--line);border-radius:6px;color:var(--ink);padding:8px;font:13px var(--mono)" placeholder="e.g. is the oversold rule decaying?">
   <button onclick="askQ()" style="background:var(--live);border:0;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer">Ask</button></div>
   <pre id="ans" style="white-space:pre-wrap;font:12px var(--mono);color:var(--ink);margin-top:8px"></pre></div>`;
  fetch('/api/report').then(r=>r.json()).then(d=>{
    document.getElementById('report').textContent=d.text;
    document.getElementById('rdate').textContent=d.date?('· '+d.date):'';});}
 document.getElementById('kpis').innerHTML=`
  <div class="card"><div class="lbl">Resolved</div><div class="kpi">${s.resolved}<small> /${s.total_signals}</small></div></div>
  <div class="card"><div class="lbl">Hit rate</div><div class="kpi ${s.hit_rate>=s.avg_entry?'pos':'neg'}">${pct(s.hit_rate)}</div>
    <div class="muted">avg entry ${fmt(s.avg_entry,2)} — must stay below hit rate</div></div>
  <div class="card"><div class="lbl">EV / bet · taker</div><div class="kpi ${s.ev_taker>0?'pos':'neg'}">${pct(s.ev_taker)}</div></div>
  <div class="card"><div class="lbl">EV / bet · maker</div><div class="kpi ${s.ev_maker>0?'pos':'neg'}">${pct(s.ev_maker)}</div></div>`;
 const cd=s.cal||[];
 const calData={datasets:[
   {label:'fair value',type:'line',data:[{x:0.4,y:0.4},{x:0.9,y:0.9}],borderColor:'#8593AC',borderDash:[6,4],pointRadius:0},
   {label:'realized',type:'bubble',data:cd.map(c=>({x:c.p,y:c.win,r:Math.min(4+Math.sqrt(c.n),14)})),backgroundColor:'#F5A623'}]};
 if(!calc){calc=new Chart(document.getElementById('cal'),{data:calData,options:{scales:{x:{min:.4,max:.9,grid:{color:'#1E2A44'},title:{display:true,text:'entry price (implied prob)'}},y:{min:.3,max:1,grid:{color:'#1E2A44'},title:{display:true,text:'realized win rate'}}},plugins:{legend:{display:false}}}});}
 else{calc.data=calData;calc.update('none')}
 const eqData={labels:s.equity_ts.map((_,i)=>i+1),datasets:[
   {label:'taker (after fee)',data:s.equity_taker,borderColor:'#E0556B',pointRadius:0,tension:.2},
   {label:'maker if filled',data:s.equity_maker,borderColor:'#3FB68B',pointRadius:0,tension:.2}]};
 if(!eq){eq=new Chart(document.getElementById('eq'),{type:'line',data:eqData,options:{scales:{x:{display:false},y:{grid:{color:'#1E2A44'}}},plugins:{legend:{labels:{color:'#8593AC'}}}}});}
 else{eq.data=eqData;eq.update('none')}
 document.getElementById('rules').innerHTML='<table><tr><th>rule</th><th>n</th><th>live win</th><th>backtest</th><th>EV taker</th></tr>'+
   (s.rules||[]).map(r=>`<tr><td>${r.rule}</td><td>${r.n}</td><td class="${r.bt&&r.win>=r.bt-0.02?'pos':'neg'}">${pct(r.win)}</td><td>${r.bt?pct(r.bt):'—'}</td><td class="${r.ev>0?'pos':'neg'}">${pct(r.ev)}</td></tr>`).join('')+'</table>';
 document.getElementById('recent').innerHTML='<table><tr><th>time</th><th>coin</th><th>side</th><th>fill</th><th>result</th><th>pnl</th></tr>'+
   (s.recent||[]).map(r=>`<tr><td>${r.t}</td><td>${r.coin}</td><td>${r.side}</td><td>${fmt(r.fill,2)}</td><td class="${r.out===r.side?'pos':'neg'}">${r.out}</td><td class="${r.pnl>0?'pos':'neg'}">${fmt(r.pnl)}</td></tr>`).join('')+'</table>';
}
async function askQ(){
 const q=document.getElementById('q').value;if(!q)return;
 const ans=document.getElementById('ans');ans.textContent='thinking…';
 const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
 ans.textContent=(await r.json()).a;}
tick();setInterval(tick,30000);
</script></body></html>"""


@app.route("/api/report")
def latest_report():
    files = sorted(glob.glob("reports/*.md"))
    if not files:
        return jsonify(dict(date=None,
            text="No analyst report yet. Run: python3 analyst.py"))
    return jsonify(dict(date=os.path.basename(files[-1])[:-3],
                        text=open(files[-1]).read()))


@app.route("/api/ask", methods=["POST"])
def ask_assist():
    from flask import request
    q = (request.get_json(silent=True) or {}).get("q", "").strip()
    if not q:
        return jsonify(dict(a="Empty question."))
    try:
        import json as _json
        from analyst import gather, SYSTEM
        from llm_client import ask
        ctx = _json.dumps(gather(), indent=1)
        a = ask(f"Current verified stats:\n{ctx}\n\nOperator question: {q}",
                provider=os.environ.get("ASSIST_PROVIDER", "nvidia"),
                system=SYSTEM, max_tokens=700)
    except Exception as e:
        a = f"Assist unavailable: {e}"
    return jsonify(dict(a=a))


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050)
