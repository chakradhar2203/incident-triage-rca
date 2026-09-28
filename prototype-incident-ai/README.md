# AI Incident Triage & RCA Assistant

Developer prototype for receiving ServiceNow incidents, embedding the incident text and logs, retrieving the three closest verified cases, generating a grounded RCA, writing the result back to ServiceNow, and learning from closed-ticket feedback.

The default requires no cloud account: it uses a deterministic 384-dimensional local embedding and in-memory cosine search. Configure Qdrant to use a real vector database. Configure an OpenAI API key to use `text-embedding-3-small` (384 dimensions) and Structured Outputs through the Responses API for the RCA; without it, the grounded deterministic synthesizer remains active.

## Run locally

```powershell
cd prototype-incident-ai
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Open the dashboard at `http://127.0.0.1:8000`. API documentation is at `http://127.0.0.1:8000/docs`.

The app starts in **local demo mode** and does not make external calls. Enter sandbox credentials in the dashboard and explicitly enable write-back when ready. Dashboard secrets are write-only, stay in process memory, disappear on restart, and are never returned by the configuration API. For longer-running deployments, set these environment variables instead:

```text
SERVICENOW_INSTANCE_URL=https://dev12345.service-now.com
SERVICENOW_USERNAME=api.integration
SERVICENOW_SECRET=replace-me
SERVICENOW_WRITE_BACK=false
OPENAI_API_KEY=replace-me
QDRANT_URL=https://replace-me.cloud.qdrant.io:6333
QDRANT_API_KEY=replace-me
QDRANT_COLLECTION=incident_resolutions
OPENAI_MODEL=gpt-5-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

Never commit real credentials. When Qdrant is configured, the bundled verified cases are embedded and upserted automatically before the first query. Closed-ticket feedback is also embedded and upserted immediately.

## ServiceNow developer sandbox

1. Sign in to the ServiceNow Developer site and open **Manage > Instance**. If no instance exists, request a Personal Developer Instance (PDI), then click **Start Building**.
2. Copy the browser origin; it looks like `https://dev12345.service-now.com`. This is the instance URL.
3. Recent PDIs restrict Basic authentication for ordinary interactive accounts. Create a dedicated machine account at **All > User Administration > Users > New**, enable **Web service access only**, and grant the incident/API roles required by the prototype. Use that account in the dashboard instead of `admin`.
4. In the PDI, use **All > Incidents > All** (classic navigation) or search the All menu for **Incidents**. Newly created records appear there. Search by the returned number, such as `INC0010123`, or open the direct link printed by `post_incident.js`.
5. Start this app, enter the URL/username/password in the dashboard, save, then click **Test PDI**. Enable PATCH write-back only after the connection succeeds.

Create an **After Insert** Business Rule on the `Incident [incident]` table. Enable Advanced and have its script POST this JSON to the public/tunneled URL ending in `/api/v1/servicenow/webhook`:

```json
{
  "sys_id": "${current.sys_id}",
  "number": "${current.number}",
  "short_description": "${current.short_description}",
  "description": "${current.description}",
  "category": "${current.category}",
  "priority": "${current.priority}",
  "raw_logs": "${current.u_raw_logs}"
}
```

In ServiceNow server-side JavaScript, use `sn_ws.RESTMessageV2`, set the request headers to JSON, build the body with `JSON.stringify`, and send it asynchronously. Do not hard-code this app's credentials in the Business Rule; the outbound webhook itself does not require the ServiceNow password.

When write-back is enabled, the app PATCHes:

```text
/api/now/table/incident/{sys_id}?sysparm_input_display_value=true
```

It appends AI-generated work notes, changes `state` to `2` (In Progress), and sets the assignment group using its display value. The target group must already exist in the sandbox; create it under **All > User Administration > Groups** when needed.

Create a second **After Update** Business Rule conditioned on the incident becoming Closed/Resolved. POST a human-verified record to `/api/v1/servicenow/resolution-feedback`:

```json
{
  "sys_id": "0123456789abcdef0123456789abcdef",
  "incident_id": "INC0012345",
  "short_description": "Short summary",
  "description": "Original incident description",
  "category": "Network",
  "root_cause": "Verified root cause",
  "resolution_notes": "What the engineer actually did",
  "assignment_group": "Network Operations Team"
}
```

The feedback endpoint updates the in-memory retrieval corpus for the current process. Persistent Qdrant upsert is intentionally not enabled until the connection and embedding provider are configured.

## API endpoints

- `POST /api/v1/prototype/ingest` — local incident analysis
- `POST /api/v1/servicenow/webhook` — ServiceNow event plus optional PATCH write-back
- `POST /api/v1/servicenow/resolution-feedback` — add a verified resolution
- `GET /api/v1/prototype/dashboard/{incident_id}` — evidence and top-three matches
- `POST /api/v1/prototype/copilot` — grounded incident Q&A
- `GET /api/v1/metrics` — RCA/dashboard metrics
- `GET` / `PUT /api/v1/config` — public status and write-only runtime configuration

Run tests with `python -m pytest -q`.

## Seed synthetic incidents with a script

The included `seed_servicenow.py` creates up to three safe synthetic incidents through the ServiceNow Table API. Keep the dashboard/API running in one terminal, then use a second terminal:

```powershell
$env:SERVICENOW_INSTANCE_URL = "https://dev12345.service-now.com"
$env:SERVICENOW_USERNAME = "admin"
python seed_servicenow.py --dry-run
python seed_servicenow.py --count 3
```

The real run prompts for the password without displaying it. Alternatively, set `SERVICENOW_SECRET` for non-interactive execution and remove it afterward:

```powershell
$env:SERVICENOW_SECRET = "your-sandbox-password"
python seed_servicenow.py --count 3
Remove-Item Env:SERVICENOW_SECRET
```

To create each incident in ServiceNow **and** immediately analyze it through the local API, add `--forward-local`:

```powershell
python seed_servicenow.py --count 3 --forward-local
```

This local-forward option does not require ngrok or another public tunnel. A ServiceNow Business Rule cannot call `127.0.0.1` on your computer; for automatic cloud-to-local webhook delivery, expose the FastAPI port through an authenticated development tunnel and configure the Business Rule with that HTTPS URL.

## Post one incident with JavaScript

Node.js 18+ is sufficient; the script uses built-in `fetch` and does not install packages. Keep the password only in the current terminal session:

```powershell
$env:SERVICENOW_INSTANCE_URL = "https://dev12345.service-now.com"
$env:SERVICENOW_USERNAME = "admin"
$env:SERVICENOW_SECRET = Read-Host "PDI password"
node post_incident.js --dry-run
node post_incident.js
Remove-Item Env:SERVICENOW_SECRET
```

For a local password form that avoids putting the password in an environment variable, set only the non-secret values and start browser mode:

```powershell
$env:SERVICENOW_INSTANCE_URL = "https://dev12345.service-now.com"
$env:SERVICENOW_USERNAME = "incident.rca.integration"
node post_incident.js --browser
```

Open `http://127.0.0.1:8765`, paste the password, and click **Create incident**. The form is served only on localhost and the script does not write the password to disk.

Browser mode keeps the credentials only in the running Node.js process and exposes `POST http://127.0.0.1:8765/run` for a local scheduler. Each call chooses a randomized incident template, creates the ServiceNow record, forwards it to the FastAPI webhook, and confirms that the RCA write-back completed. Keep both the FastAPI dashboard and the browser-mode script running while a recurrence is active.

The generated response is appended to the ServiceNow incident under **Notes > Activity / Work notes** with the heading **AI-GENERATED RCA — ENGINEER REVIEW REQUIRED**. It is also available in the local dashboard and its incident-detail API for the lifetime of the FastAPI process.

## Post an incident every two minutes from the dashboard

The dashboard now owns the recurring demo schedule; the separate browser poster and Codex automations are not required. Save and test the ServiceNow credentials, enable **ServiceNow PATCH write-back**, then click **Start every 2 minutes** in the **Recurring incident poster** panel. The backend creates one randomized incident immediately and another every 120 seconds. Each record is analyzed and updated with RCA Work Notes, state, and assignment group. The panel displays the last incident, next run, write-back status, and any error.

Click **Stop** to end the schedule. The schedule is process-local and automatically stops when FastAPI shuts down; it does not survive a restart. A cloud deployment should replace this process-local loop with Cloud Scheduler or another durable scheduler while keeping the same create-and-process operation.

The successful response prints the incident number, `sys_id`, and a direct PDI link. Open **All > Incidents > All** if the direct link is not convenient. The Table API create call does not itself invoke the local RCA app; either configure the ServiceNow Business Rule/webhook described above or run the Python seeder with `--forward-local` for a no-tunnel end-to-end test.
