"""Browser dashboard and dashboard-specific API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from integrations import ConfigStore, ServiceNowClient
from incident_scheduler import RecurringIncidentScheduler
from llm_synthesis import ClaudeAPIError, ClaudeRCASynthesizer
from rca_engine import RCAEngine
from schemas import RuntimeConfigUpdate
from servicenow_installer import install_review_controls


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
        "engineer_review": engine.get_review(incident_id),
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


@router.post("/api/v1/servicenow/install-review-controls")
def install_servicenow_review_controls(request: Request) -> dict[str, object]:
    config = request.app.state.config_store.get()
    if not config.is_servicenow_ready:
        raise HTTPException(status_code=400, detail="Save and test PDI credentials first")
    try:
        return install_review_controls(ServiceNowClient(config))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PDI installation failed: {exc}") from exc


@router.post("/api/v1/claude/test-connection")
def test_claude_connection(request: Request) -> dict[str, object]:
    config = request.app.state.config_store.get()
    if not config.is_claude_ready:
        raise HTTPException(status_code=400, detail="Save a valid Claude API key first")
    try:
        return ClaudeRCASynthesizer(config).test_connection()
    except ClaudeAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
:root{--bg:#08111f;--panel:#101d2e;--text:#e7eef8;--muted:#91a4bd;--cyan:#38d9c5;--line:#263a52;--amber:#f5bd63;--red:#ff7a8a}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 75% 0,#183252 0,transparent 34%),var(--bg);color:var(--text);font:14px/1.5 Inter,Segoe UI,sans-serif}.shell{max-width:1420px;margin:auto;padding:28px}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.brand{font-size:22px;font-weight:700}.brand span{color:var(--cyan)}.badge{padding:7px 12px;border:1px solid var(--line);border-radius:99px;color:var(--muted)}.badge.ok{color:var(--cyan);border-color:#226e69}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:18px}.panel{background:linear-gradient(145deg,rgba(20,37,58,.97),rgba(12,25,41,.97));border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 18px 50px #0005}.wide{grid-column:1/-1}.title{display:flex;align-items:center;justify-content:space-between;margin-bottom:15px}.title h2{font-size:14px;text-transform:uppercase;letter-spacing:1.5px;margin:0;color:#bed0e5}.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:14px}.metric strong{display:block;font-size:26px;margin-top:5px}.metric small,.hint{color:var(--muted)}label{display:block;color:var(--muted);font-size:12px;margin:11px 0 5px}input,textarea,select{width:100%;background:#091625;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px 11px;outline:none}input:focus,textarea:focus{border-color:var(--cyan)}textarea{min-height:78px;resize:vertical}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:16px}button{border:0;border-radius:9px;padding:10px 15px;font-weight:700;cursor:pointer;background:var(--cyan);color:#06201d}button.secondary{background:#253951;color:var(--text)}button.danger{background:#5d2934;color:#ffdce1}.flow{display:grid;grid-template-columns:repeat(6,1fr);gap:7px}.step{padding:12px 9px;background:#0b192a;border:1px solid var(--line);border-radius:10px;color:var(--muted);min-height:74px}.step b{display:block;color:var(--text);font-size:12px;margin:4px 0}.step em{font-style:normal;color:var(--cyan);font-size:11px}.result{white-space:pre-wrap;background:#081421;border:1px solid var(--line);padding:14px;border-radius:10px;min-height:118px;color:#cbd8e8}.reviewbox{margin-top:14px;padding:14px;border:1px solid var(--line);border-radius:10px;background:#0b192a}.hidden{display:none}.hint{font-size:12px;margin:8px 0 0}.switch{display:flex;align-items:center;gap:8px;margin-top:14px}.switch input{width:auto}.toast{min-height:22px;color:var(--cyan);margin-top:8px}.warning{color:var(--amber)}@media(max-width:900px){.grid{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}.flow{grid-template-columns:1fr 1fr}.formgrid{grid-template-columns:1fr}}
</style></head><body><main class="shell">
<div class="top"><div class="brand">Incident <span>Intelligence</span> Console</div><div id="mode" class="badge">Loading configuration…</div></div>
<section class="metrics wide"><div class="panel metric"><small>Incidents analyzed</small><strong>—</strong></div><div class="panel metric"><small>Knowledge cases</small><strong>—</strong></div><div class="panel metric"><small>Average confidence</small><strong>—</strong></div><div class="panel metric"><small>High confidence</small><strong>—</strong></div><div class="panel metric"><small>Pending reviews</small><strong>—</strong></div></section>
<div class="grid" style="margin-top:18px">
<section class="panel wide"><div class="title"><h2>Operational pipeline</h2><span id="retrievalMode" class="badge">Loading retrieval…</span></div><div class="flow"><div class="step"><em>01</em><b>Webhook</b>Extract payload</div><div class="step"><em>02</em><b>Vector retrieve</b>Top-3 cases</div><div class="step"><em>03</em><b>Synthesize</b>RCA + confidence</div><div class="step"><em>04</em><b>Write back</b>Notes + routing</div><div class="step"><em>05</em><b>Human review</b>Execute safely</div><div class="step"><em>06</em><b>Feedback</b>Upsert resolution</div></div></section>
<section class="panel"><div class="title"><h2>Sandbox connection</h2><span class="hint">Secrets are write-only</span></div><form id="configForm"><label>ServiceNow instance URL</label><input name="servicenow_instance_url" placeholder="https://dev12345.service-now.com"><div class="formgrid"><div><label>ServiceNow username</label><input name="servicenow_username" placeholder="admin"></div><div><label>Password / access token</label><input name="servicenow_secret" type="password" placeholder="••••••••"></div><div><label>Claude API key (optional)</label><input name="anthropic_api_key" type="password" placeholder="sk-ant-…"></div><div><label>Claude model</label><input name="claude_model" placeholder="claude-haiku-4-5-20251001"></div></div><label>Firebase project ID (optional)</label><input name="firebase_project_id" placeholder="your-firebase-project"><div class="formgrid"><div><label>Firestore database</label><input name="firestore_database" placeholder="(default)"></div><div><label>Firestore collection</label><input name="firestore_collection" placeholder="incident_resolutions"></div></div><label class="switch"><input name="write_back_enabled" type="checkbox"> Enable ServiceNow PATCH write-back</label><p class="hint warning">Firestore uses Application Default Credentials from gcloud login or GOOGLE_APPLICATION_CREDENTIALS. Never paste a Firebase service-account JSON into this form or commit it.</p><div class="actions"><button type="submit">Save runtime config</button><button type="button" class="secondary" id="testConnection">Test PDI</button><button type="button" class="secondary" id="installPdi">Install PDI review controls</button><button type="button" class="secondary" id="testClaude">Test Claude</button><button type="button" class="secondary" id="refresh">Refresh</button></div><div id="configToast" class="toast"></div></form></section>
<section class="panel"><div class="title"><h2>Recurring incident poster</h2><span id="schedulerBadge" class="badge">Stopped</span></div><p>Create one randomized ServiceNow incident immediately, then another every two minutes. Each incident runs through RAG analysis and ServiceNow write-back.</p><div class="actions"><button type="button" id="startScheduler">Start every 2 minutes</button><button type="button" class="secondary" id="stopScheduler">Stop</button></div><div id="schedulerStatus" class="result" style="min-height:100px;margin-top:16px">Scheduler is stopped.</div><p class="hint warning">The schedule runs only while this FastAPI application is running. Stop it before leaving a sandbox unattended.</p></section>
<section class="panel"><div class="title"><h2>Test incident</h2><span class="hint">Safe local simulation</span></div><form id="incidentForm"><div class="formgrid"><div><label>Incident number</label><input name="incident_id" value="INC-DEMO-001"></div><div><label>Category</label><select name="category"><option>Application</option><option>Database</option><option>Infrastructure</option><option>Network</option></select></div></div><label>Short description</label><input name="short_description" value="Checkout service crashes under load"><label>Description</label><textarea name="description">Checkout pods restart after the latest release during peak traffic.</textarea><label>Raw logs</label><textarea name="raw_logs">java.lang.OutOfMemoryError: Java heap space&#10;OOMKilled exit code 137&#10;HTTP 503</textarea><div class="actions"><button type="submit">Analyze incident</button><button type="button" class="secondary" id="clearResult">Clear</button></div></form></section>
<section class="panel wide"><div class="title"><h2>RCA analysis</h2><span id="confidence" class="badge">Awaiting incident</span></div><div id="result" class="result">Submit a test incident or POST a ServiceNow event to /api/v1/servicenow/webhook.</div><div id="reviewPanel" class="reviewbox hidden"><div class="title"><h2>Engineer validation</h2><span id="reviewState" class="badge">Pending</span></div><div class="formgrid"><div><label>Engineer name</label><input id="reviewer" placeholder="Engineer name or user ID"></div><div><label>Validated root cause (optional)</label><input id="validatedRootCause" placeholder="Confirmed cause after investigation"></div></div><label>Review notes</label><textarea id="reviewNotes" placeholder="Evidence checked, corrections required, or reason for rejection"></textarea><div class="actions"><button type="button" data-review="approved">Approve RCA</button><button type="button" class="secondary" data-review="needs_evidence">Request more evidence</button><button type="button" class="danger" data-review="rejected">Reject RCA</button></div><div id="reviewToast" class="toast"></div><p class="hint warning">Approval records the review only. It does not resolve the incident or publish a knowledge article.</p></div></section>
</div></main><script>
const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let currentIncidentId='';
async function load(){const [c,m]=await Promise.all([fetch('/api/v1/config').then(r=>r.json()),fetch('/api/v1/metrics').then(r=>r.json())]);$('#mode').textContent=c.mode==='sandbox-connected'?'● Sandbox connected':'○ Local demo mode';$('#mode').className='badge '+(c.mode==='sandbox-connected'?'ok':'');$('#retrievalMode').textContent=`${m.retrieval_backend} · ${m.embedding_provider} · ${m.generation_provider}`;for(const k of ['servicenow_instance_url','servicenow_username','claude_model','firebase_project_id','firestore_database','firestore_collection'])if(c[k])$(`[name=${k}]`).value=c[k];$('[name=write_back_enabled]').checked=c.write_back_enabled;const vals=[m.incidents_analyzed,m.knowledge_cases,Math.round(m.average_confidence*100)+'%',m.high_confidence_incidents,m.pending_engineer_reviews];document.querySelectorAll('.metric strong').forEach((e,i)=>e.textContent=vals[i]);}
function renderScheduler(s){$('#schedulerBadge').textContent=s.active?'● Running':'Stopped';$('#schedulerBadge').className='badge '+(s.active?'ok':'');const last=s.last_result?`Last incident: ${s.last_result.number}\n${s.last_result.short_description}\nCase: ${s.last_result.knowledge_case_id}\nReview: ${s.last_result.engineer_review_required?'REQUIRED':'STANDARD'}\nWrite-back: ${s.last_result.write_back}`:`No incident has been created in this session.\n${s.scenario_count||0} aligned scenarios are available.`;const next=s.next_run_at?`\nNext run: ${new Date(s.next_run_at).toLocaleString()}`:'';const error=s.last_error?`\nError: ${s.last_error}`:'';$('#schedulerStatus').textContent=`${last}${next}${error}`;}
async function loadScheduler(){const r=await fetch('/api/v1/recurring-incidents'),d=await r.json();if(r.ok)renderScheduler(d);}
$('#configForm').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target),body=Object.fromEntries(f.entries());body.write_back_enabled=$('[name=write_back_enabled]').checked;const r=await fetch('/api/v1/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();$('#configToast').textContent=r.ok?'Configuration saved for this session.':(d.detail||'Could not save configuration');e.target.querySelectorAll('input[type=password]').forEach(x=>x.value='');await load()};
$('#incidentForm').onsubmit=async e=>{e.preventDefault();const body=Object.fromEntries(new FormData(e.target));body.priority='P1';body.recent_changes=['Latest application release'];$('#result').textContent='Analyzing…';const r=await fetch('/api/v1/prototype/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok){$('#result').textContent=JSON.stringify(d,null,2);return}currentIncidentId=d.incident_id;$('#reviewPanel').classList.remove('hidden');$('#reviewState').textContent='Pending';$('#confidence').textContent=Math.round(d.confidence_score*100)+'% confidence';$('#confidence').className='badge ok';$('#result').innerHTML='<b>Probable root cause</b>\n'+esc(d.probable_root_cause)+'\n\n<b>Recommended actions</b>\n'+d.recommended_actions.map((x,i)=>`${i+1}. ${esc(x)}`).join('\n')+'\n\n<b>Commands</b>\n'+d.commands.map(x=>`$ ${esc(x.command)}\n  ${esc(x.explanation)}`).join('\n');await load()};
$('#refresh').onclick=load;$('#clearResult').onclick=()=>{$('#result').textContent='Awaiting an incident.'};load().catch(()=>{$('#mode').textContent='Dashboard API unavailable'});
$('#testConnection').onclick=async()=>{const r=await fetch('/api/v1/servicenow/test-connection',{method:'POST'}),d=await r.json();$('#configToast').textContent=r.ok?'ServiceNow PDI connection successful.':(d.detail||'Connection failed')};
$('#installPdi').onclick=async()=>{if(!confirm('Create missing AI review fields, choices, and form buttons in the configured PDI? Existing matching records will not be overwritten.'))return;$('#configToast').textContent='Installing PDI review controls…';const r=await fetch('/api/v1/servicenow/install-review-controls',{method:'POST'}),d=await r.json();$('#configToast').textContent=r.ok?`PDI controls installed: ${d.created_count} created, ${d.existing_count} already present.`:(d.detail||'PDI installation failed')};
$('#testClaude').onclick=async()=>{const r=await fetch('/api/v1/claude/test-connection',{method:'POST'}),d=await r.json();$('#configToast').textContent=r.ok?`Claude API successful: ${d.model} (${d.input_tokens??0} input / ${d.output_tokens??0} output tokens).`:(d.detail||'Claude API test failed')};
$('#startScheduler').onclick=async()=>{const r=await fetch('/api/v1/recurring-incidents/start',{method:'POST'}),d=await r.json();if(!r.ok){$('#schedulerStatus').textContent=d.detail||'Could not start scheduler';return}renderScheduler(d);setTimeout(loadScheduler,1500)};
$('#stopScheduler').onclick=async()=>{const r=await fetch('/api/v1/recurring-incidents/stop',{method:'POST'}),d=await r.json();renderScheduler(d)};
document.querySelectorAll('[data-review]').forEach(button=>button.onclick=async()=>{if(!currentIncidentId){$('#reviewToast').textContent='Analyze an incident first.';return}const reviewer=$('#reviewer').value.trim(),notes=$('#reviewNotes').value.trim();if(!reviewer||!notes){$('#reviewToast').textContent='Engineer name and review notes are required.';return}const body={decision:button.dataset.review,reviewer,notes,validated_root_cause:$('#validatedRootCause').value.trim()};const r=await fetch(`/api/v1/prototype/reviews/${encodeURIComponent(currentIncidentId)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),d=await r.json();if(!r.ok){$('#reviewToast').textContent=d.detail||'Review could not be stored';return}$('#reviewState').textContent=body.decision.replace('_',' ');$('#reviewState').className='badge ok';$('#reviewToast').textContent='Review recorded. Knowledge publication remains separate.';await load()});
loadScheduler();setInterval(loadScheduler,5000);
</script></body></html>'''
