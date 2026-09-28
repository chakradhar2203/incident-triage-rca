"""FastAPI entry point for the incident RCA prototype."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request

from copilot import router as copilot_router
from dashboard import router as dashboard_router
from incident_scheduler import RecurringIncidentScheduler
from integrations import ConfigStore, ServiceNowClient
from log_parser import parse_logs
from rca_engine import RCAEngine
from schemas import IncidentPayload, RCAResult, ResolutionFeedback, ServiceNowWebhookPayload


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.config_store = ConfigStore()
    app.state.rca_engine = RCAEngine(config_store=app.state.config_store)
    app.state.incident_scheduler = RecurringIncidentScheduler(
        app.state.config_store,
        lambda payload: _process_servicenow_payload(payload, app),
    )
    try:
        yield
    finally:
        await app.state.incident_scheduler.stop()


app = FastAPI(
    title="AI Incident Triage & RCA Assistant",
    description="ServiceNow sandbox webhook, grounded RCA retrieval, write-back, feedback, and dashboard.",
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(dashboard_router)
app.include_router(copilot_router)


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/prototype/ingest", response_model=RCAResult, tags=["incidents"])
def ingest_incident(payload: IncidentPayload, request: Request) -> RCAResult:
    engine: RCAEngine = request.app.state.rca_engine
    evidence = parse_logs(payload.raw_logs)
    return engine.analyze(payload, evidence)


def _work_notes(result: RCAResult) -> str:
    evidence = "\n".join(f"- {item}" for item in result.evidence_correlation)
    actions = "\n".join(f"{index}. {item}" for index, item in enumerate(result.recommended_actions, 1))
    commands = "\n".join(
        f"$ {item.command}\n  {item.explanation}" + (" [REQUIRES APPROVAL]" if item.is_destructive else "")
        for item in result.commands
    )
    return (
        "AI-GENERATED RCA — ENGINEER REVIEW REQUIRED\n\n"
        f"Probable root cause: {result.probable_root_cause}\n"
        f"Confidence: {result.confidence_score:.1%}\n\nEvidence:\n{evidence}\n\n"
        f"Recommended actions:\n{actions}\n\nSuggested commands:\n{commands}"
    )


@app.post("/api/v1/servicenow/webhook", tags=["servicenow"])
def servicenow_webhook(payload: ServiceNowWebhookPayload, request: Request) -> dict[str, object]:
    """Receive a ServiceNow Business Rule event and optionally write the RCA back."""

    return _process_servicenow_payload(payload, request.app)


def _process_servicenow_payload(payload: ServiceNowWebhookPayload, app: FastAPI) -> dict[str, object]:
    """Analyze one ServiceNow payload and perform the configured write-back."""

    engine: RCAEngine = app.state.rca_engine
    incident = payload.to_incident()
    result = engine.analyze(incident, parse_logs(incident.raw_logs))
    config = app.state.config_store.get()
    write_back: dict[str, object] = {"attempted": False, "status": "disabled"}
    if config.write_back_enabled:
        if not config.is_servicenow_ready:
            write_back = {"attempted": False, "status": "not_configured"}
        else:
            try:
                updated = ServiceNowClient(config).patch_incident(
                    payload.sys_id, _work_notes(result), result.suggested_team
                )
                write_back = {"attempted": True, "status": "updated", "record": updated}
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"RCA completed but ServiceNow PATCH failed: {exc}") from exc
    return {"rca": result.model_dump(), "write_back": write_back}


@app.post("/api/v1/servicenow/resolution-feedback", tags=["servicenow"])
def resolution_feedback(payload: ResolutionFeedback, request: Request) -> dict[str, object]:
    """Add a human-verified closed-ticket resolution to the local retrieval corpus."""

    engine: RCAEngine = request.app.state.rca_engine
    engine.add_verified_resolution(
        {
            "incident_id": payload.incident_id,
            "short_description": payload.short_description,
            "description": payload.description,
            "category": payload.category,
            "error_patterns": [],
            "root_cause": payload.root_cause,
            "suggested_team": payload.assignment_group,
            "recommended_actions": [payload.resolution_notes],
            "verified_commands": [],
        }
    )
    return {"stored": True, "incident_id": payload.incident_id, "knowledge_cases": engine.metrics()["knowledge_cases"]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
