"""Runtime configuration and ServiceNow Table API integration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import RLock
from urllib.parse import urlparse

import httpx


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


@dataclass(slots=True)
class RuntimeConfig:
    servicenow_instance_url: str = field(default_factory=lambda: _env("SERVICENOW_INSTANCE_URL"))
    servicenow_username: str = field(default_factory=lambda: _env("SERVICENOW_USERNAME"))
    servicenow_secret: str = field(default_factory=lambda: _env("SERVICENOW_SECRET"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    qdrant_url: str = field(default_factory=lambda: _env("QDRANT_URL"))
    qdrant_api_key: str = field(default_factory=lambda: _env("QDRANT_API_KEY"))
    qdrant_collection: str = field(default_factory=lambda: _env("QDRANT_COLLECTION") or "incident_resolutions")
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL") or "gpt-5-mini")
    embedding_model: str = field(default_factory=lambda: _env("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small")
    write_back_enabled: bool = field(default_factory=lambda: _env("SERVICENOW_WRITE_BACK").lower() == "true")

    def public_view(self) -> dict[str, object]:
        return {
            "servicenow_instance_url": self.servicenow_instance_url,
            "servicenow_username": self.servicenow_username,
            "servicenow_secret_configured": bool(self.servicenow_secret),
            "openai_api_key_configured": bool(self.openai_api_key),
            "qdrant_url": self.qdrant_url,
            "qdrant_api_key_configured": bool(self.qdrant_api_key),
            "qdrant_collection": self.qdrant_collection,
            "openai_model": self.openai_model,
            "embedding_model": self.embedding_model,
            "write_back_enabled": self.write_back_enabled,
            "mode": "sandbox-connected" if self.is_servicenow_ready else "local-demo",
        }

    @property
    def is_servicenow_ready(self) -> bool:
        return bool(self.servicenow_instance_url and self.servicenow_username and self.servicenow_secret)

    @property
    def is_openai_ready(self) -> bool:
        """Reject obvious placeholders before any billable external request is attempted."""

        return self.openai_api_key.startswith("sk-") and len(self.openai_api_key) >= 20


class ConfigStore:
    """Process-local, write-only secret store suitable for a developer prototype."""

    def __init__(self) -> None:
        self._value = RuntimeConfig()
        self._lock = RLock()

    def get(self) -> RuntimeConfig:
        with self._lock:
            return self._value

    def update(self, values: dict[str, object]) -> RuntimeConfig:
        with self._lock:
            for key, value in values.items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                if not hasattr(self._value, key):
                    continue
                setattr(self._value, key, value.strip() if isinstance(value, str) else value)
            if self._value.servicenow_instance_url:
                self._value.servicenow_instance_url = self._value.servicenow_instance_url.rstrip("/")
            return self._value


def _validated_instance_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("ServiceNow instance URL must be an absolute http(s) URL")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("ServiceNow instance URL must use HTTPS")
    return value.rstrip("/")


class ServiceNowClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def patch_incident(self, sys_id: str, work_notes: str, assignment_group: str) -> dict[str, object]:
        base_url = _validated_instance_url(self.config.servicenow_instance_url)
        url = f"{base_url}/api/now/table/incident/{sys_id}"
        response = httpx.patch(
            url,
            params={"sysparm_input_display_value": "true", "sysparm_exclude_reference_link": "true"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            auth=(self.config.servicenow_username, self.config.servicenow_secret),
            json={"work_notes": work_notes, "state": "2", "assignment_group": assignment_group},
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("result", payload)

    def create_incident(self, incident: dict[str, str]) -> dict[str, object]:
        """Create one incident and return the ServiceNow record."""

        base_url = _validated_instance_url(self.config.servicenow_instance_url)
        response = httpx.post(
            f"{base_url}/api/now/table/incident",
            params={"sysparm_input_display_value": "false", "sysparm_exclude_reference_link": "true"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            auth=(self.config.servicenow_username, self.config.servicenow_secret),
            json=incident,
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("result", payload)

    def test_connection(self) -> dict[str, object]:
        base_url = _validated_instance_url(self.config.servicenow_instance_url)
        response = httpx.get(
            f"{base_url}/api/now/table/incident",
            params={"sysparm_limit": "1", "sysparm_fields": "sys_id,number,short_description"},
            headers={"Accept": "application/json"},
            auth=(self.config.servicenow_username, self.config.servicenow_secret),
            timeout=15.0,
        )
        response.raise_for_status()
        return {"connected": True, "sample_count": len(response.json().get("result", []))}
