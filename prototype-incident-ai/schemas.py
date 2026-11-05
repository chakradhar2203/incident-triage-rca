"""Pydantic models shared by the prototype API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from integrations import valid_google_project_id


class APIModel(BaseModel):
    """Strict base model used for public API contracts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IncidentPayload(APIModel):
    incident_id: str = Field(min_length=1, max_length=100)
    short_description: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=10_000)
    category: str = Field(min_length=1, max_length=100)
    priority: str = Field(min_length=1, max_length=30)
    raw_logs: str = Field(default="", max_length=500_000)
    recent_changes: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("recent_changes")
    @classmethod
    def remove_blank_changes(cls, changes: list[str]) -> list[str]:
        return [change.strip() for change in changes if change.strip()]


class ServiceNowWebhookPayload(APIModel):
    sys_id: str = Field(min_length=1, max_length=100)
    number: str = Field(default="", max_length=100)
    short_description: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=10_000)
    category: str = Field(default="Other", max_length=100)
    priority: str = Field(default="3", max_length=30)
    raw_logs: str = Field(default="", max_length=500_000)
    recent_changes: list[str] = Field(default_factory=list, max_length=100)
    knowledge_case_id: str = Field(default="", max_length=100)
    force_engineer_review: bool = False
    review_reason: str = Field(default="", max_length=2_000)

    def to_incident(self) -> IncidentPayload:
        return IncidentPayload(
            incident_id=self.number or self.sys_id,
            short_description=self.short_description,
            description=self.description,
            category=self.category or "Other",
            priority=self.priority or "3",
            raw_logs=self.raw_logs,
            recent_changes=self.recent_changes,
        )


class RuntimeConfigUpdate(APIModel):
    servicenow_instance_url: str | None = Field(default=None, max_length=500)
    servicenow_username: str | None = Field(default=None, max_length=200)
    servicenow_secret: str | None = Field(default=None, max_length=1_000)
    anthropic_api_key: str | None = Field(default=None, max_length=1_000)
    claude_model: str | None = Field(default=None, max_length=200)
    firebase_project_id: str | None = Field(default=None, max_length=200)
    firestore_database: str | None = Field(default=None, max_length=200)
    firestore_collection: str | None = Field(default=None, max_length=200)
    write_back_enabled: bool | None = None

    @field_validator("firebase_project_id")
    @classmethod
    def reject_secret_as_firebase_project(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        cleaned = value.strip()
        if not valid_google_project_id(cleaned):
            raise ValueError(
                "Firebase project ID must be a lowercase Google project ID, for example incident-triage-rca; "
                "never paste an API key into this field"
            )
        return cleaned


class ResolutionFeedback(APIModel):
    sys_id: str = Field(min_length=1, max_length=100)
    incident_id: str = Field(min_length=1, max_length=100)
    short_description: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=10_000)
    category: str = Field(default="Other", max_length=100)
    root_cause: str = Field(min_length=1, max_length=10_000)
    resolution_notes: str = Field(min_length=1, max_length=20_000)
    assignment_group: str = Field(default="Service Desk", max_length=200)


class EngineerReviewPayload(APIModel):
    decision: Literal["approved", "needs_evidence", "rejected"]
    reviewer: str = Field(min_length=1, max_length=200)
    notes: str = Field(min_length=1, max_length=5_000)
    validated_root_cause: str = Field(default="", max_length=10_000)


class CommandDetail(APIModel):
    command: str = Field(min_length=1)
    is_destructive: bool
    explanation: str = Field(min_length=1)


class RCAResult(APIModel):
    incident_id: str
    probable_root_cause: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    evidence_correlation: list[str]
    suggested_team: str
    recommended_actions: list[str]
    commands: list[CommandDetail]


class CopilotQuery(APIModel):
    incident_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2_000)


class CopilotResponse(APIModel):
    answer: str
