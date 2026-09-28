"""Browser dashboard and dashboard-specific API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from integrations import ConfigStore, ServiceNowClient
from incident_scheduler import RecurringIncidentScheduler
from rca_engine import RCAEngine
from schemas import RuntimeConfigUpdate


router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page() -> str:
    return _DASHBOARD_HTML


@router.get("/api/v1/prototype/dashboard/{incident_id}")
def get_dashboard(incident_id: str, request: Request) -> dict[str, Any]:
    engine: RCAEngine = request.app.state.rca_engine
    context = engine.get_context(incident_id)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' has not been ingested")
    return {
        "incident": context.incident.model_dump(),
        "rca_report": context.result.model_dump(),
        "log_evidence": context.log_evidence.to_dict(),
        "matching_historical_cases": [match.public_view() for match in context.historical_matches],
    }


@router.get("/api/v1/config")
def get_config(request: Request) -> dict[str, object]:
    store: ConfigStore = request.app.state.config_store
    return store.get().public_view()


@router.put("/api/v1/config")
def update_config(payload: RuntimeConfigUpdate, request: Request) -> dict[str, object]:
    store: ConfigStore = request.app.state.config_store
    return store.update(payload.model_dump(exclude_none=True)).public_view()


@router.post("/api/v1/servicenow/test-connection")
def test_servicenow_connection(request: Request) -> dict[str, object]:
    config = request.app.state.config_store.get()
    if not config.is_servicenow_ready:
        raise HTTPException(status_code=400, detail="Save the PDI URL, username, and password/access token first")
    try:
        return ServiceNowClient(config).test_connection()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ServiceNow connection test failed: {exc}") from exc


@router.get("/api/v1/recurring-incidents")
def recurring_incident_status(request: Request) -> dict[str, object]:
    scheduler: RecurringIncidentScheduler = request.app.state.incident_scheduler
    return scheduler.status()


@router.post("/api/v1/recurring-incidents/start")
async def start_recurring_incidents(request: Request) -> dict[str, object]:
    scheduler: RecurringIncidentScheduler = request.app.state.incident_scheduler
    try:
        return scheduler.start()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/recurring-incidents/stop")
async def stop_recurring_incidents(request: Request) -> dict[str, object]:
    scheduler: RecurringIncidentScheduler = request.app.state.incident_scheduler
    return await scheduler.stop()


@router.get("/api/v1/metrics")
def get_metrics(request: Request) -> dict[str, object]:
    engine: RCAEngine = request.app.state.rca_engine
    metrics = engine.metrics()
    metrics["configuration"] = request.app.state.config_store.get().public_view()
    return metrics


_DASHBOARD_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Incident Intelligence Console</title>
<style>
:root{--bg:#08111f;--panel:#101d2e;--text:#e7eef8;--muted:#91a4bd;--cyan:#38d9c5;--line:#263a52;--amber:#f5bd63}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 75% 0,#183252 0,transparent 34%),var(--bg);color:var(--text);font:14px/1.5 Inter,Segoe UI,sans-serif}.shell{max-width:1420px;margin:auto;padding:28px}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.brand{font-size:22px;font-weight:700}.brand span{color:var(--cyan)}.badge{padding:7px 12px;border:1px solid var(--line);border-radius:99px;color:var(--muted)}.badge.ok{color:var(--cyan);border-color:#226e69}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:18px}.panel{background:linear-gradient(145deg,rgba(20,37,58,.97),rgba(12,25,41,.97));border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 18px 50px #0005}.wide{grid-column:1/-1}.title{display:flex;align-items:center;justify-content:space-between;margin-bottom:15px}.title h2{font-size:14px;text-transform:uppercase;letter-spacing:1.5px;margin:0;color:#bed0e5}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric strong{display:block;font-size:26px;margin-top:5px}.metric small,.hint{color:var(--muted)}label{display:block;color:var(--muted);font-size:12px;margin:11px 0 5px}input,textarea,select{width:100%;background:#091625;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px 11px;outline:none}input:focus,textarea:focus{border-color:var(--cyan)}textarea{min-height:78px;resize:vertical}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.actions{display:flex;gap:9px;margin-top:16px}button{border:0;border-radius:9px;padding:10px 15px;font-weight:700;cursor:pointer;background:var(--cyan);color:#06201d}button.secondary{background:#253951;color:var(--text)}.flow{display:grid;grid-template-columns:repeat(6,1fr);gap:7px}.step{padding:12px 9px;background:#0b192a;border:1px solid var(--line);border-radius:10px;color:var(--muted);min-height:74px}.step b{display:block;color:var(--text);font-size:12px;margin:4px 0}.step em{font-style:normal;color:var(--cyan);font-size:11px}.result{white-space:pre-wrap;background:#081421;border:1px solid var(--line);padding:14px;border-radius:10px;min-height:118px;color:#cbd8e8}.hint{font-size:12px;margin:8px 0 0}.switch{display:flex;align-items:center;gap:8px;margin-top:14px}.switch input{width:auto}.toast{min-height:22px;color:var(--cyan);margin-top:8px}.warning{color:var(--amber)}@media(max-width:900px){.grid{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}.flow{grid-template-columns:1fr 1fr}.formgrid{grid-template-columns:1fr}}
</style></head><body><main class="shell">
<div class="top"><div class="brand">Incident <span>Intelligence</span> Console</div><div id="mode" class="badge">Loading configuration…</div></div>
<section class="metrics wide"><div class="panel metric"><small>Incidents analyzed</small><strong>—</strong></div><div class="panel metric"><small>Knowledge cases</small><strong>—</strong></div><div class="panel metric"><small>Average confidence</small><strong>—</strong></div><div class="panel metric"><small>High confidence</small><strong>—</strong></div></section>
<div class="grid" style="margin-top:18px">
<section class="panel wide"><div class="title"><h2>Operational pipeline</h2><span id="retrievalMode" class="badge">Loading retrieval…</span></div><div class="flow"><div class="step"><em>01</em><b>Webhook</b>Extract payload</div><div class="step"><em>02</em><b>Vector retrieve</b>Top-3 cases</div><div class="step"><em>03</em><b>Synthesize</b>RCA + confidence</div><div class="step"><em>04</em><b>Write back</b>Notes + routing</div><div class="step"><em>05</em><b>Human review</b>Execute safely</div><div class="step"><em>06</em><b>Feedback</b>Upsert resolution</div></div></section>
<section class="panel"><div class="title"><h2>Sandbox connection</h2><span class="hint">Secrets are write-only</span></div><form id="configForm"><label>ServiceNow instance URL</label><input name="servicenow_instance_url" placeholder="https://dev12345.service-now.com"><div class="formgrid"><div><label>ServiceNow username</label><input name="servicenow_username" placeholder="admin"></div><div><label>Password / access token</label><input name="servicenow_secret" type="password" placeholder="••••••••"></div><div><label>OpenAI API key (optional)</label><input name="openai_api_key" type="password" placeholder="sk-…"></div><div><label>Qdrant API key (optional)</label><input name="qdrant_api_key" type="password" placeholder="qdrant-key…"></div></div><label>Qdrant URL (optional)</label><input name="qdrant_url" placeholder="https://your-cluster.cloud.qdrant.io:6333"><label>Collection</label><input name="qdrant_collection" placeholder="incident_resolutions"><label class="switch"><input name="write_back_enabled" type="checkbox"> Enable ServiceNow PATCH write-back</label><p class="hint warning">Use a dedicated sandbox account. Credentials remain only in this running process and are never returned by the API.</p><div class="actions"><button type="submit">Save runtime config</button><button type="button" class="secondary" id="testConnection">Test PDI</button><button type="button" class="secondary" id="refresh">Refresh</button></div><div id="configToast" class="toast"></div></form></section>
<section class="panel"><div class="title"><h2>Recurring incident poster</h2><span id="schedulerBadge" class="badge">Stopped</span></div><p>Create one randomized ServiceNow incident immediately, then another every two minutes. Each incident runs through RAG analysis and ServiceNow write-back.</p><div class="actions"><button type="button" id="startScheduler">Start every 2 minutes</button><button type="button" class="secondary" id="stopScheduler">Stop</button></div><div id="schedulerStatus" class="result" style="min-height:100px;margin-top:16px">Scheduler is stopped.</div><p class="hint warning">The schedule runs only while this FastAPI application is running. Stop it before leaving a sandbox unattended.</p></section>
<section class="panel"><div class="title"><h2>Test incident</h2><span class="hint">Safe local simulation</span></div><form id="incidentForm"><div class="formgrid"><div><label>Incident number</label><input name="incident_id" value="INC-DEMO-001"></div><div><label>Category</label><select name="category"><option>Application</option><option>Database</option><option>Infrastructure</option><option>Network</option></select></div></div><label>Short description</label><input name="short_description" value="Checkout service crashes under load"><label>Description</label><textarea name="description">Checkout pods restart after the latest release during peak traffic.</textarea><label>Raw logs</label><textarea name="raw_logs">java.lang.OutOfMemoryError: Java heap space&#10;OOMKilled exit code 137&#10;HTTP 503</textarea><div class="actions"><button type="submit">Analyze incident</button><button type="button" class="secondary" id="clearResult">Clear</button></div></form></section>
<section class="panel wide"><div class="title"><h2>RCA analysis</h2><span id="confidence" class="badge">Awaiting incident</span></div><div id="result" class="result">Submit a test incident or POST a ServiceNow event to /api/v1/servicenow/webhook.</div></section>
</div></main><script>
const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){const [c,m]=await Promise.all([fetch('/api/v1/config').then(r=>r.json()),fetch('/api/v1/metrics').then(r=>r.json())]);$('#mode').textContent=c.mode==='sandbox-connected'?'● Sandbox connected':'○ Local demo mode';$('#mode').className='badge '+(c.mode==='sandbox-connected'?'ok':'');$('#retrievalMode').textContent=`${m.retrieval_backend} · ${m.embedding_provider}`;for(const k of ['servicenow_instance_url','servicenow_username','qdrant_url','qdrant_collection'])if(c[k])$(`[name=${k}]`).value=c[k];$('[name=write_back_enabled]').checked=c.write_back_enabled;const vals=[m.incidents_analyzed,m.knowledge_cases,Math.round(m.average_confidence*100)+'%',m.high_confidence_incidents];document.querySelectorAll('.metric strong').forEach((e,i)=>e.textContent=vals[i]);}
function renderScheduler(s){$('#schedulerBadge').textContent=s.active?'● Running':'Stopped';$('#schedulerBadge').className='badge '+(s.active?'ok':'');const last=s.last_result?`Last incident: ${s.last_result.number}\n${s.last_result.short_description}\nWrite-back: ${s.last_result.write_back}`:'No incident has been created in this session.';const next=s.next_run_at?`\nNext run: ${new Date(s.next_run_at).toLocaleString()}`:'';const error=s.last_error?`\nError: ${s.last_error}`:'';$('#schedulerStatus').textContent=`${last}${next}${error}`;}
async function loadScheduler(){const r=await fetch('/api/v1/recurring-incidents'),d=await r.json();if(r.ok)renderScheduler(d);}
$('#configForm').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target),body=Object.fromEntries(f.entries());body.write_back_enabled=$('[name=write_back_enabled]').checked;const r=await fetch('/api/v1/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();$('#configToast').textContent=r.ok?'Configuration saved for this session.':(d.detail||'Could not save configuration');e.target.querySelectorAll('input[type=password]').forEach(x=>x.value='');await load()};
$('#incidentForm').onsubmit=async e=>{e.preventDefault();const body=Object.fromEntries(new FormData(e.target));body.priority='P1';body.recent_changes=['Latest application release'];$('#result').textContent='Analyzing…';const r=await fetch('/api/v1/prototype/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok){$('#result').textContent=JSON.stringify(d,null,2);return}$('#confidence').textContent=Math.round(d.confidence_score*100)+'% confidence';$('#confidence').className='badge ok';$('#result').innerHTML='<b>Probable root cause</b>\n'+esc(d.probable_root_cause)+'\n\n<b>Recommended actions</b>\n'+d.recommended_actions.map((x,i)=>`${i+1}. ${esc(x)}`).join('\n')+'\n\n<b>Commands</b>\n'+d.commands.map(x=>`$ ${esc(x.command)}\n  ${esc(x.explanation)}`).join('\n');await load()};
$('#refresh').onclick=load;$('#clearResult').onclick=()=>{$('#result').textContent='Awaiting an incident.'};load().catch(()=>{$('#mode').textContent='Dashboard API unavailable'});
$('#testConnection').onclick=async()=>{const r=await fetch('/api/v1/servicenow/test-connection',{method:'POST'}),d=await r.json();$('#configToast').textContent=r.ok?'ServiceNow PDI connection successful.':(d.detail||'Connection failed')};
$('#startScheduler').onclick=async()=>{const r=await fetch('/api/v1/recurring-incidents/start',{method:'POST'}),d=await r.json();if(!r.ok){$('#schedulerStatus').textContent=d.detail||'Could not start scheduler';return}renderScheduler(d);setTimeout(loadScheduler,1500)};
$('#stopScheduler').onclick=async()=>{const r=await fetch('/api/v1/recurring-incidents/stop',{method:'POST'}),d=await r.json();renderScheduler(d)};
loadScheduler();setInterval(loadScheduler,5000);
</script></body></html>'''
