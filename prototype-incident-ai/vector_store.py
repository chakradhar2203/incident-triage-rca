"""Embedding and vector-store adapters used by the RCA retrieval pipeline."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from typing import Any

import httpx

from integrations import RuntimeConfig, _validated_instance_url


VECTOR_SIZE = 384
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.:/-]{1,}", re.I)


def case_text(case: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(case.get("short_description", "")),
            str(case.get("description", "")),
            str(case.get("category", "")),
            " ".join(map(str, case.get("error_patterns", []))),
            str(case.get("root_cause", "")),
            " ".join(map(str, case.get("recommended_actions", []))),
        ]
    )


def local_embedding(text: str, dimensions: int = VECTOR_SIZE) -> list[float]:
    """Create a deterministic normalized feature-hashing embedding for offline demo mode."""

    vector = [0.0] * dimensions
    tokens = [token.casefold() for token in _TOKEN.findall(text)]
    features = tokens + [f"{left}::{right}" for left, right in zip(tokens, tokens[1:])]
    for feature in features:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


class EmbeddingClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    @property
    def provider(self) -> str:
        return "openai" if self.config.is_openai_ready else "local-hashing"

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not self.config.is_openai_ready:
            return [local_embedding(text) for text in texts]
        response = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.config.openai_api_key}"},
            json={
                "model": self.config.embedding_model,
                "input": texts,
                "dimensions": VECTOR_SIZE,
                "encoding_format": "float",
            },
            timeout=30.0,
        )
        response.raise_for_status()
        items = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]


class LocalVectorStore:
    name = "in-memory-vector"

    def search(
        self, query: list[float], cases: list[dict[str, Any]], vectors: list[list[float]], limit: int
    ) -> list[tuple[dict[str, Any], float]]:
        return sorted(zip(cases, (cosine(query, vector) for vector in vectors)), key=lambda item: item[1], reverse=True)[
            :limit
        ]


class QdrantVectorStore:
    name = "qdrant"

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.base_url = _validated_instance_url(config.qdrant_url)
        self.collection = config.qdrant_collection
        self.headers = {"Content-Type": "application/json"}
        if config.qdrant_api_key:
            self.headers["api-key"] = config.qdrant_api_key

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = httpx.request(method, f"{self.base_url}{path}", headers=self.headers, timeout=30.0, **kwargs)
        response.raise_for_status()
        return response

    def ensure_collection(self) -> None:
        response = httpx.get(f"{self.base_url}/collections/{self.collection}", headers=self.headers, timeout=15.0)
        if response.status_code == 404:
            self._request(
                "PUT", f"/collections/{self.collection}", json={"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}}
            )
            return
        response.raise_for_status()

    def upsert(self, cases: list[dict[str, Any]], vectors: list[list[float]]) -> None:
        self.ensure_collection()
        points = [
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"incident-rca:{case['incident_id']}")),
                "vector": vector,
                "payload": case,
            }
            for case, vector in zip(cases, vectors)
        ]
        self._request("PUT", f"/collections/{self.collection}/points", params={"wait": "true"}, json={"points": points})

    def search(self, query: list[float], limit: int) -> list[tuple[dict[str, Any], float]]:
        self.ensure_collection()
        response = self._request(
            "POST",
            f"/collections/{self.collection}/points/search",
            json={"vector": query, "limit": limit, "with_payload": True},
        )
        return [(item["payload"], float(item["score"])) for item in response.json().get("result", [])]
