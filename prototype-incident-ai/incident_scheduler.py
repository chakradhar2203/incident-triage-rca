"""Process-local recurring ServiceNow incident generator for the dashboard demo."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from integrations import ConfigStore, ServiceNowClient
from schemas import ServiceNowWebhookPayload


def _load_templates() -> tuple[dict[str, object], ...]:
    source = Path(__file__).with_name("knowledge_cases_candidates.json")
    with source.open("r", encoding="utf-8") as handle:
        cases = json.load(handle)
    templates: list[dict[str, object]] = []
    for case in cases:
        scenario = case.get("synthetic_incident")
        if not isinstance(scenario, dict):
            continue
        templates.append(
            {
                **scenario,
                "knowledge_case_id": case["incident_id"],
                "short_description": case["short_description"],
                "description": case["description"],
            }
        )
    if not templates:
        raise ValueError("No synthetic incident templates were found in the knowledge-case dataset")
    return tuple(templates)


class RecurringIncidentScheduler:
    """Create a random incident immediately and then at a fixed interval."""

    def __init__(
        self,
        config_store: ConfigStore,
        processor: Callable[[ServiceNowWebhookPayload], dict[str, object]],
        interval_seconds: int = 120,
    ) -> None:
        self._config_store = config_store
        self._processor = processor
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._last_result: dict[str, object] | None = None
        self._last_error: str | None = None
        self._last_run_at: datetime | None = None
        self._next_run_at: datetime | None = None
        self._templates = _load_templates()
        self._template_index = 0

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, object]:
        return {
            "active": self.active,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "next_run_at": self._next_run_at.isoformat() if self._next_run_at else None,
            "last_result": self._last_result,
            "last_error": self._last_error,
            "scenario_count": len(self._templates),
            "next_scenario_index": self._template_index + 1,
        }

    def start(self) -> dict[str, object]:
        if self.active:
            return self.status()
        config = self._config_store.get()
        if not config.is_servicenow_ready:
            raise ValueError("Save and test the ServiceNow URL, username, and password first")
        if not config.write_back_enabled:
            raise ValueError("Enable ServiceNow PATCH write-back before starting recurring incidents")
        self._last_error = None
        self._task = asyncio.create_task(self._loop(), name="recurring-servicenow-incidents")
        return self.status()

    async def stop(self) -> dict[str, object]:
        task = self._task
        self._task = None
        self._next_run_at = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self.status()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._create_and_process)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                self._last_result = None
            self._next_run_at = datetime.now(UTC) + timedelta(seconds=self.interval_seconds)
            await asyncio.sleep(self.interval_seconds)

    def _create_and_process(self) -> None:
        template = self._templates[self._template_index]
        self._template_index = (self._template_index + 1) % len(self._templates)
        created_at = datetime.now(UTC)
        description = f"{template['description']} Synthetic dashboard incident created at {created_at.isoformat()}."
        logs = str(template["raw_logs"])
        case_id = str(template["knowledge_case_id"])
        review_required = bool(template.get("requires_engineer_review", True))
        review_reason = str(template.get("review_reason", "Engineer validation is required."))
        record = ServiceNowClient(self._config_store.get()).create_incident(
            {
                "short_description": str(template["short_description"]),
                "description": description,
                "category": str(template["category"]),
                "impact": str(template.get("impact", "2")),
                "urgency": str(template.get("urgency", "2")),
                "work_notes": (
                    f"SYNTHETIC PDI INCIDENT — {case_id}\n"
                    f"Engineer review priority: {'REQUIRED' if review_required else 'STANDARD'}\n"
                    f"Review reason: {review_reason}\n\nSource logs:\n{logs}"
                ),
            }
        )
        payload = ServiceNowWebhookPayload(
            sys_id=str(record["sys_id"]),
            number=str(record.get("number", "")),
            short_description=str(template["short_description"]),
            description=description,
            category=str(template["category"]),
            priority="3",
            raw_logs=logs,
            recent_changes=[str(item) for item in template.get("recent_changes", [])],
            knowledge_case_id=case_id,
            force_engineer_review=review_required,
            review_reason=review_reason,
        )
        pipeline = self._processor(payload)
        write_back = pipeline.get("write_back", {})
        if not isinstance(write_back, dict) or write_back.get("status") != "updated":
            raise RuntimeError(f"Incident created but RCA write-back did not complete: {write_back}")
        self._last_run_at = created_at
        self._last_error = None
        self._last_result = {
            "number": record.get("number"),
            "sys_id": record.get("sys_id"),
            "short_description": template["short_description"],
            "write_back": "updated",
            "knowledge_case_id": case_id,
            "engineer_review_required": review_required,
            "review_reason": review_reason,
        }
