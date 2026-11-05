"""Embedding and vector-store adapters used by the RCA retrieval pipeline."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from integrations import RuntimeConfig


VECTOR_SIZE = 384
LOCAL_EMBEDDING_MODEL = "local-hashing-v1"
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
    """Deterministic, no-network embedding provider used for documents and queries."""

    provider = LOCAL_EMBEDDING_MODEL

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [local_embedding(text) for text in texts]


class LocalVectorStore:
    name = "in-memory-vector"

    def search(
        self, query: list[float], cases: list[dict[str, Any]], vectors: list[list[float]], limit: int
    ) -> list[tuple[dict[str, Any], float]]:
        return sorted(zip(cases, (cosine(query, vector) for vector in vectors)), key=lambda item: item[1], reverse=True)[
            :limit
        ]


class FirestoreVectorStore:
    """Cloud Firestore adapter for verified incident-resolution vectors."""

    name = "firestore-vector"

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        try:
            from google.cloud import firestore
        except ImportError as exc:  # pragma: no cover - exercised only when optional dependency is missing
            raise RuntimeError("Install google-cloud-firestore to use Firestore vector retrieval") from exc
        self._firestore = firestore
        self._client = firestore.Client(project=config.firebase_project_id, database=config.firestore_database)
        self._collection = self._client.collection(config.firestore_collection)

    @staticmethod
    def _document_id(incident_id: str) -> str:
        return hashlib.sha256(incident_id.encode("utf-8")).hexdigest()

    def upsert(self, cases: list[dict[str, Any]], vectors: list[list[float]]) -> None:
        from google.cloud.firestore_v1.vector import Vector

        batch = self._client.batch()
        for case, vector in zip(cases, vectors):
            payload = dict(case)
            payload.update(
                {
                    "verified": True,
                    "embedding_model": LOCAL_EMBEDDING_MODEL,
                    "embedding_dimension": VECTOR_SIZE,
                    "embedding": Vector(vector),
                    "updated_at": self._firestore.SERVER_TIMESTAMP,
                }
            )
            reference = self._collection.document(self._document_id(str(case["incident_id"])))
            batch.set(reference, payload, merge=True)
        batch.commit()

    def search(self, query: list[float], limit: int) -> list[tuple[dict[str, Any], float]]:
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        vector_query = self._collection.find_nearest(
            vector_field="embedding",
            query_vector=Vector(query),
            distance_measure=DistanceMeasure.COSINE,
            limit=max(limit, 10),
            distance_result_field="vector_distance",
        )
        results: list[tuple[dict[str, Any], float]] = []
        for document in vector_query.stream():
            case = document.to_dict()
            distance = float(case.pop("vector_distance", 2.0))
            case.pop("embedding", None)
            if not case.get("verified") or case.get("embedding_model") != LOCAL_EMBEDDING_MODEL:
                continue
            # Firestore cosine distance is 1 - cosine similarity. Return the raw
            # cosine similarity expected by RCAEngine and LocalVectorStore.
            results.append((case, max(-1.0, min(1.0, 1.0 - distance))))
            if len(results) == limit:
                break
        return results
