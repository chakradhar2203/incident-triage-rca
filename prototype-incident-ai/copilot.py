"""Grounded interactive Q&A for previously ingested incidents."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from rca_engine import IncidentContext, RCAEngine
from schemas import CopilotQuery, CopilotResponse


router = APIRouter(prefix="/api/v1/prototype", tags=["copilot"])


def _answer_question(context: IncidentContext, question: str) -> str:
    query = question.casefold()
    result = context.result
    if any(term in query for term in ("evidence", "log", "signal")):
        return "The assessment is supported by: " + "; ".join(result.evidence_correlation)
    if any(term in query for term in ("root cause", "cause", "why")):
        return f"The probable root cause is: {result.probable_root_cause} Confidence: {result.confidence_score:.1%}."
    if any(term in query for term in ("action", "fix", "resolve", "next")):
        return "Recommended actions: " + " ".join(
            f"{index}. {action}" for index, action in enumerate(result.recommended_actions, start=1)
        )
    if any(term in query for term in ("command", "terminal", "shell")):
        rendered = []
        for detail in result.commands:
            safety = "DESTRUCTIVE - approval required" if detail.is_destructive else "non-destructive"
            rendered.append(f"`{detail.command}` ({safety}): {detail.explanation}")
        return "Relevant commands: " + "; ".join(rendered)
    if any(term in query for term in ("team", "owner", "assign")):
        return f"Route this incident to the {result.suggested_team}."
    if any(term in query for term in ("similar", "history", "historical")):
        return "Closest historical cases: " + "; ".join(
            f"{match.case['incident_id']} ({match.score:.1%} match)" for match in context.historical_matches
        )
    return (
        f"For incident {result.incident_id}, the leading hypothesis is '{result.probable_root_cause}' "
        f"({result.confidence_score:.1%} confidence). The suggested owner is {result.suggested_team}. "
        "Ask about evidence, actions, commands, ownership, or similar historical cases for more detail."
    )


@router.post("/copilot", response_model=CopilotResponse)
def ask_copilot(query: CopilotQuery, request: Request) -> CopilotResponse:
    engine: RCAEngine = request.app.state.rca_engine
    context = engine.get_context(query.incident_id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{query.incident_id}' has not been ingested",
        )
    return CopilotResponse(answer=_answer_question(context, query.question))
