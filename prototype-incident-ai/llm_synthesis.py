"""Optional structured LLM synthesis over retrieved, verified incident context."""

from __future__ import annotations

import json

import httpx

from integrations import RuntimeConfig
from schemas import RCAResult


class ClaudeAPIError(RuntimeError):
    """Safe provider error that excludes request credentials and payload content."""


class ClaudeRCASynthesizer:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.config.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        if not response.is_error:
            return
        request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = {}
        error = error_payload.get("error", {}) if isinstance(error_payload, dict) else {}
        message = error.get("message") if isinstance(error, dict) else None
        detail = str(message or response.reason_phrase or "Unknown Claude API error")[:1_000]
        request_suffix = f" (request ID: {request_id})" if request_id else ""
        raise ClaudeAPIError(f"Claude API returned HTTP {response.status_code}: {detail}{request_suffix}")

    def test_connection(self) -> dict[str, object]:
        """Validate authentication, model access, billing, and Messages API inference."""

        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers=self._headers,
            json={
                "model": self.config.claude_model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Reply with OK."}],
            },
            timeout=30.0,
        )
        self._raise_for_error(response)
        payload = response.json()
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        return {
            "connected": True,
            "model": payload.get("model", self.config.claude_model),
            "request_id": payload.get("id"),
            "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
            "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
        }

    def synthesize(self, prompt: str) -> RCAResult:
        schema = RCAResult.model_json_schema()
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers=self._headers,
            json={
                "model": self.config.claude_model,
                "max_tokens": 1800,
                "temperature": 0,
                "system": (
                    "Act as a cautious SRE. Use only the supplied incident evidence and verified historical "
                    "context. If there is no historical context, say so and reason only from observable evidence. "
                    "Do not claim that commands were executed. Prefer diagnostic and reversible actions."
                ),
                "messages": [{"role": "user", "content": prompt}],
                "tools": [
                    {
                        "name": "submit_incident_rca",
                        "description": "Return the final grounded incident RCA in the required schema.",
                        "input_schema": schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": "submit_incident_rca"},
            },
            timeout=60.0,
        )
        self._raise_for_error(response)
        payload = response.json()
        tool_inputs = [
            block.get("input")
            for block in payload.get("content", [])
            if block.get("type") == "tool_use" and block.get("name") == "submit_incident_rca"
        ]
        if not tool_inputs:
            raise ValueError("Claude response did not contain the required structured RCA tool output")
        value = tool_inputs[0]
        if isinstance(value, str):
            value = json.loads(value)
        return RCAResult.model_validate(value)
