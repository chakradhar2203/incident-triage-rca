#!/usr/bin/env node
/** Create a synthetic incident through the ServiceNow Table API using Node 18+ fetch. */

import http from "node:http";

const args = new Set(process.argv.slice(2));
const dryRun = args.has("--dry-run");
const browserMode = args.has("--browser");
const instance = (process.env.SERVICENOW_INSTANCE_URL || "").replace(/\/$/, "");
const username = process.env.SERVICENOW_USERNAME || "admin";
const secret = process.env.SERVICENOW_SECRET || "";

if (!instance || !instance.startsWith("https://")) {
  console.error("Set SERVICENOW_INSTANCE_URL to your https://devXXXXX.service-now.com PDI URL.");
  process.exit(2);
}
if (!dryRun && !browserMode && !secret) {
  console.error("Set SERVICENOW_SECRET for this terminal session. The script never writes or prints it.");
  process.exit(2);
}

const incidentTemplates = [
  {
    short_description: "Checkout API pods restart during peak traffic",
    description: "Checkout requests fail after the latest release.",
    category: "software",
    logs: "java.lang.OutOfMemoryError: Java heap space\nOOMKilled exit code 137\nHTTP 503",
  },
  {
    short_description: "Production host filesystem is almost full",
    description: "Application writes fail while the root filesystem reports critical utilization.",
    category: "hardware",
    logs: "No space left on device\nFilesystem /dev/sda1 98% used\nwrite failed errno=28",
  },
  {
    short_description: "Orders API cannot connect to PostgreSQL",
    description: "Database connections started timing out after a configuration rollout.",
    category: "database",
    logs: "FATAL: remaining connection slots are reserved\nconnection pool exhausted\nHTTP 500",
  },
  {
    short_description: "Internal service name intermittently fails DNS lookup",
    description: "Application nodes cannot consistently resolve the inventory service.",
    category: "network",
    logs: "Temporary failure in name resolution\nSERVFAIL inventory.internal\nDNS timeout",
  },
  {
    short_description: "TLS certificate validation fails for upstream API",
    description: "Requests to the partner endpoint fail during the TLS handshake.",
    category: "software",
    logs: "x509: certificate has expired or is not yet valid\nTLS handshake failed\nHTTP 502",
  },
];

function randomPayload() {
  const template = incidentTemplates[Math.floor(Math.random() * incidentTemplates.length)];
  return {
    short_description: template.short_description,
    description: `${template.description} Synthetic recurring RCA demo incident created at ${new Date().toISOString()}.`,
    category: template.category,
    impact: "2",
    urgency: "2",
    work_notes: `Synthetic source logs:\n${template.logs}`,
    _raw_logs: template.logs,
  };
}
const url = `${instance}/api/now/table/incident?sysparm_input_display_value=false&sysparm_exclude_reference_link=true`;

async function createIncident(apiUsername, apiSecret, incidentPayload = randomPayload()) {
  const { _raw_logs, ...tablePayload } = incidentPayload;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Basic ${Buffer.from(`${apiUsername}:${apiSecret}`).toString("base64")}`,
    },
    body: JSON.stringify(tablePayload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`ServiceNow returned HTTP ${response.status}: ${JSON.stringify(body)}`);
  const result = {
    created: true,
    number: body.result?.number,
    sys_id: body.result?.sys_id,
    open_in_pdi: `${instance}/now/nav/ui/classic/params/target/incident.do%3Fsys_id%3D${body.result?.sys_id}`,
  };
  const localResponse = await fetch("http://127.0.0.1:8001/api/v1/servicenow/webhook", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sys_id: result.sys_id,
      number: result.number,
      short_description: incidentPayload.short_description,
      description: incidentPayload.description,
      category: incidentPayload.category,
      priority: "3",
      raw_logs: _raw_logs,
    }),
  });
  const analysis = await localResponse.json().catch(() => ({}));
  if (!localResponse.ok) throw new Error(`Local RCA pipeline returned HTTP ${localResponse.status}: ${JSON.stringify(analysis)}`);
  return { ...result, rca: analysis.rca, write_back: analysis.write_back?.status };
}

if (browserMode) {
  let runtimeCredentials = null;
  let lastResult = null;
  const page = (message = "") => `<!doctype html><meta charset="utf-8"><title>ServiceNow JS Poster</title>
  <style>body{max-width:680px;margin:60px auto;padding:24px;font:16px system-ui;background:#08111f;color:#e7eef8}input,button{width:100%;padding:12px;margin:6px 0 16px;box-sizing:border-box}button{background:#38d9c5;border:0;font-weight:700}.msg{white-space:pre-wrap}</style>
  <h1>ServiceNow Recurring Incident Poster</h1><p>Configure the in-memory credentials and create the first randomized incident. The local <code>/run</code> endpoint creates subsequent incidents and sends each through the RCA pipeline.</p>
  <form method="post" action="/create"><label>Instance</label><input value="${instance}" disabled><label>Username</label><input name="username" value="${username}"><label>Password</label><input name="secret" type="password" autocomplete="off"><button>Configure and create now</button></form><div class="msg">${message}</div>`;
  const server = http.createServer(async (request, response) => {
    if (request.method === "GET" && request.url === "/status") {
      response.setHeader("Content-Type", "application/json");
      return response.end(JSON.stringify({
        configured: Boolean(runtimeCredentials),
        configured_username: runtimeCredentials?.username || null,
        secret_length: runtimeCredentials?.secret?.length || 0,
        lastResult,
      }));
    }
    if (request.method === "POST" && request.url === "/run") {
      response.setHeader("Content-Type", "application/json");
      if (!runtimeCredentials) { response.statusCode = 409; return response.end(JSON.stringify({ error: "Credentials are not configured" })); }
      try {
        lastResult = await createIncident(runtimeCredentials.username, runtimeCredentials.secret);
        return response.end(JSON.stringify(lastResult));
      } catch (error) {
        response.statusCode = 502;
        return response.end(JSON.stringify({ error: String(error) }));
      }
    }
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    if (request.method === "GET") return response.end(page(runtimeCredentials ? "Configured. Scheduled runs may call POST /run." : ""));
    if (request.method !== "POST" || request.url !== "/create") { response.statusCode = 404; return response.end("Not found"); }
    let data = "";
    for await (const chunk of request) data += chunk;
    const form = new URLSearchParams(data);
    try {
      runtimeCredentials = { username: form.get("username") || username, secret: form.get("secret") || "" };
      const result = await createIncident(runtimeCredentials.username, runtimeCredentials.secret);
      lastResult = result;
      response.end(page(`Created ${result.number}\n${result.sys_id}\n${result.open_in_pdi}`));
    } catch (error) {
      response.statusCode = 502;
      response.end(page(String(error)));
    }
  });
  server.listen(8765, "127.0.0.1", () => console.log("ServiceNow JS poster ready at http://127.0.0.1:8765"));
} else if (dryRun) {
  console.log(JSON.stringify({ method: "POST", url, payload: randomPayload() }, null, 2));
} else {
  try {
    console.log(JSON.stringify(await createIncident(username, secret), null, 2));
  } catch (error) {
    console.error(String(error));
    process.exitCode = 1;
  }
}
