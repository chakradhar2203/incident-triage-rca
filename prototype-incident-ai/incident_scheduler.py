"""Process-local recurring ServiceNow incident generator for the dashboard demo."""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import Callable

from integrations import ConfigStore, ServiceNowClient
from schemas import ServiceNowWebhookPayload


_TEMPLATES = (
    {
        "short_description": "Checkout API pods restart during peak traffic",
        "description": "Checkout requests fail after the latest release.",
        "category": "software",
        "logs": "java.lang.OutOfMemoryError: Java heap space\nOOMKilled exit code 137\nHTTP 503",
    },
    {
        "short_description": "Production host filesystem is almost full",
        "description": "Application writes fail while the root filesystem reports critical utilization.",
        "category": "hardware",
        "logs": "No space left on device\nFilesystem /dev/sda1 98% used\nwrite failed errno=28",
    },
    {
        "short_description": "Orders API cannot connect to PostgreSQL",
        "description": "Database connections started timing out after a configuration rollout.",
        "category": "database",
        "logs": "FATAL: remaining connection slots are reserved\nconnection pool exhausted\nHTTP 500",
    },
    {
        "short_description": "Internal service name intermittently fails DNS lookup",
        "description": "Application nodes cannot consistently resolve the inventory service.",
        "category": "network",
        "logs": "Temporary failure in name resolution\nSERVFAIL inventory.internal\nDNS timeout",
    },
    {
        "short_description": "TLS certificate validation fails for upstream API",
        "description": "Requests to the partner endpoint fail during the TLS handshake.",
        "category": "software",
        "logs": "x509: certificate has expired or is not yet valid\nTLS handshake failed\nHTTP 502",
    },
)


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
        template = random.choice(_TEMPLATES)
        created_at = datetime.now(UTC)
        description = f"{template['description']} Synthetic dashboard incident created at {created_at.isoformat()}."
        record = ServiceNowClient(self._config_store.get()).create_incident(
            {
                "short_description": template["short_description"],
                "description": description,
                "category": template["category"],
                "impact": "2",
                "urgency": "2",
                "work_notes": f"Synthetic source logs:\n{template['logs']}",
            }
        )
        payload = ServiceNowWebhookPayload(
            sys_id=str(record["sys_id"]),
            number=str(record.get("number", "")),
            short_description=template["short_description"],
            description=description,
            category=template["category"],
            priority="3",
            raw_logs=template["logs"],
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
        }
