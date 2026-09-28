"""Optional structured LLM synthesis over retrieved, verified incident context."""

from __future__ import annotations

import json

import httpx

from integrations import RuntimeConfig
from schemas import RCAResult


class OpenAIRCASynthesizer:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def synthesize(self, prompt: str) -> RCAResult:
        schema = RCAResult.model_json_schema()
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.config.openai_api_key}", "Content-Type": "application/json"},
            json={
                "model": self.config.openai_model,
                "store": False,
                "instructions": (
                    "Act as a cautious SRE. Use only the supplied incident and verified historical context. "
                    "Do not claim that suggested commands were executed. Prefer diagnostic and reversible actions."
                ),
                "input": prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "incident_rca",
                        "strict": True,
                        "schema": schema,
                    }
                },
                "max_output_tokens": 1800,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        chunks = [
            content.get("text", "")
            for item in payload.get("output", [])
            if item.get("type") == "message"
            for content in item.get("content", [])
            if content.get("type") == "output_text"
        ]
        if not chunks:
            raise ValueError("OpenAI response did not contain structured output text")
        return RCAResult.model_validate(json.loads("".join(chunks)))
