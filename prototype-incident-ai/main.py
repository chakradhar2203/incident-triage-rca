"""FastAPI entry point for the incident RCA prototype."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request

from copilot import router as copilot_router
from dashboard import router as dashboard_router
from incident_scheduler import RecurringIncidentScheduler
from integrations import ConfigStore, ServiceNowClient
from llm_synthesis import ClaudeAPIError
from log_parser import parse_logs
from rca_engine import RCAEngine
from schemas import EngineerReviewPayload, IncidentPayload, RCAResult, ResolutionFeedback, ServiceNowWebhookPayload


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
    try:
        return engine.analyze(payload, evidence)
    except ClaudeAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _review_disposition(result: RCAResult, payload: ServiceNowWebhookPayload) -> dict[str, object]:
    reasons: list[str] = []
    if payload.force_engineer_review:
        reasons.append(payload.review_reason or "The source scenario requires engineer validation.")
    if result.confidence_score < 0.75:
        reasons.append(f"RCA confidence is below the 75% review threshold ({result.confidence_score:.1%}).")
    if result.probable_root_cause.casefold().startswith("unverified hypothesis"):
        reasons.append("No sufficiently strong engineer-verified historical match was found.")
    if any(command.is_destructive for command in result.commands):
        reasons.append("At least one suggested command is potentially disruptive.")
    return {
        "status": "pending",
        "required": bool(reasons),
        "priority": "required" if reasons else "standard",
        "reasons": reasons or ["Standard engineer acknowledgement is required before operational use."],
        "knowledge_case_id": payload.knowledge_case_id,
    }


def _work_notes(result: RCAResult, review: dict[str, object]) -> str:
    evidence = "\n".join(f"- {item}" for item in result.evidence_correlation)
    actions = "\n".join(f"{index}. {item}" for index, item in enumerate(result.recommended_actions, 1))
    commands = "\n".join(
        f"$ {item.command}\n  {item.explanation}" + (" [REQUIRES APPROVAL]" if item.is_destructive else "")
        for item in result.commands
    )
    review_reasons = "\n".join(f"- {item}" for item in review["reasons"])
    return (
        "AI-GENERATED RCA — ENGINEER REVIEW REQUIRED\n\n"
        f"Review priority: {str(review['priority']).upper()}\n"
        f"Knowledge case: {review.get('knowledge_case_id') or 'retrieved dynamically'}\n"
        f"Review reasons:\n{review_reasons}\n\n"
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
    review = _review_disposition(result, payload)
    config = app.state.config_store.get()
    write_back: dict[str, object] = {"attempted": False, "status": "disabled"}
    if config.write_back_enabled:
        if not config.is_servicenow_ready:
            write_back = {"attempted": False, "status": "not_configured"}
        else:
            try:
                updated = ServiceNowClient(config).patch_incident(
                    payload.sys_id, _work_notes(result, review), result.suggested_team
                )
                write_back = {"attempted": True, "status": "updated", "record": updated}
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"RCA completed but ServiceNow PATCH failed: {exc}") from exc
    return {"rca": result.model_dump(), "engineer_review": review, "write_back": write_back}


@app.post("/api/v1/prototype/reviews/{incident_id}", tags=["reviews"])
def submit_engineer_review(
    incident_id: str, payload: EngineerReviewPayload, request: Request
) -> dict[str, object]:
    """Record an engineer decision; approval alone does not publish knowledge."""

    engine: RCAEngine = request.app.state.rca_engine
    try:
        review = engine.submit_review(incident_id, payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' has not been ingested") from exc
    return {"stored": True, "review": review, "promoted_to_knowledge": False}


@app.post("/api/v1/servicenow/resolution-feedback", tags=["servicenow"])
def resolution_feedback(payload: ResolutionFeedback, request: Request) -> dict[str, object]:
    """Add a human-verified closed-ticket resolution to the local retrieval corpus."""

    engine: RCAEngine = request.app.state.rca_engine
    engine.add_verified_resolution(
        {
            "incident_id": payload.incident_id,
            "servicenow_sys_id": payload.sys_id,
            "short_description": payload.short_description,
            "description": payload.description,
            "category": payload.category,
            "error_patterns": [],
            "root_cause": payload.root_cause,
            "suggested_team": payload.assignment_group,
            "recommended_actions": [payload.resolution_notes],
            "verified_commands": [],
            "review_status": "approved",
        }
    )
    return {"stored": True, "incident_id": payload.incident_id, "knowledge_cases": engine.metrics()["knowledge_cases"]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
