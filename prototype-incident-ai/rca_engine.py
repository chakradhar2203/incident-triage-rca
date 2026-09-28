"""Vector retrieval and grounded RCA synthesis."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from integrations import ConfigStore, RuntimeConfig
from llm_synthesis import OpenAIRCASynthesizer
from log_parser import ParsedLogEvidence
from schemas import CommandDetail, IncidentPayload, RCAResult
from vector_store import EmbeddingClient, LocalVectorStore, QdrantVectorStore, case_text


_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}", re.I)
_DESTRUCTIVE = re.compile(
    r"(?:^|\s)(?:rm(?:\s|$)|kill\s+-9(?:\s|$)|drop\s+(?:database|table|schema)|systemctl\s+stop(?:\s|$)|reboot(?:\s|$))",
    re.I,
)
_STOP_WORDS = {"the", "and", "for", "with", "from", "that", "this", "after", "into", "its", "are", "was"}


@dataclass(frozen=True, slots=True)
class HistoricalMatch:
    case: dict[str, Any]
    score: float
    reasons: tuple[str, ...]
    retrieval_backend: str = "in-memory-vector"

    def public_view(self) -> dict[str, Any]:
        return {
            "incident_id": self.case["incident_id"],
            "short_description": self.case["short_description"],
            "category": self.case["category"],
            "root_cause": self.case["root_cause"],
            "match_score": round(self.score, 3),
            "match_reasons": list(self.reasons),
            "retrieval_backend": self.retrieval_backend,
        }


@dataclass(frozen=True, slots=True)
class IncidentContext:
    incident: IncidentPayload
    result: RCAResult
    log_evidence: ParsedLogEvidence
    historical_matches: tuple[HistoricalMatch, ...]
    system_prompt: str


def is_destructive_command(command: str) -> bool:
    """Conservatively flag commands containing explicitly destructive operations."""

    return bool(_DESTRUCTIVE.search(command))


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _WORD.findall(value) if token.casefold() not in _STOP_WORDS}


class RCAEngine:
    """Thread-safe store and deterministic local few-shot RCA engine."""

    def __init__(self, dataset_path: Path | None = None, config_store: ConfigStore | None = None) -> None:
        source = dataset_path or Path(__file__).with_name("mock_dataset.json")
        with source.open("r", encoding="utf-8") as dataset_file:
            loaded: object = json.load(dataset_file)
        if not isinstance(loaded, list) or not loaded:
            raise ValueError("Historical incident dataset must be a non-empty JSON array")
        self._dataset: list[dict[str, Any]] = loaded
        self._store: dict[str, IncidentContext] = {}
        self._lock = RLock()
        self._config_store = config_store or ConfigStore()
        self._qdrant_seeded_for: tuple[str, str, str] | None = None

    def _score_case(
        self, incident: IncidentPayload, evidence: ParsedLogEvidence, case: dict[str, Any]
    ) -> HistoricalMatch:
        score = 0.0
        reasons: list[str] = []
        if incident.category.casefold() == str(case["category"]).casefold():
            score += 0.30
            reasons.append(f"same category: {incident.category}")

        incoming_signatures = {value.casefold() for value in evidence.error_signatures + evidence.status_codes}
        case_patterns = {str(value).casefold() for value in case.get("error_patterns", [])}
        signature_hits = sorted(
            pattern for pattern in case_patterns if any(pattern in value or value in pattern for value in incoming_signatures)
        )
        if signature_hits:
            score += min(0.50, 0.25 + 0.10 * len(signature_hits))
            reasons.append("matching signatures: " + ", ".join(signature_hits))

        incident_text = " ".join(
            [incident.short_description, incident.description, *incident.recent_changes, incident.raw_logs[:20_000]]
        )
        case_text = " ".join(
            [str(case.get("short_description", "")), str(case.get("description", "")), " ".join(case.get("error_patterns", []))]
        )
        incoming_tokens, case_tokens = _tokens(incident_text), _tokens(case_text)
        overlap = incoming_tokens & case_tokens
        union = incoming_tokens | case_tokens
        similarity = len(overlap) / len(union) if union else 0.0
        score += min(0.20, similarity * 1.5)
        if overlap:
            reasons.append("shared terms: " + ", ".join(sorted(overlap)[:8]))
        return HistoricalMatch(case=case, score=min(score, 1.0), reasons=tuple(reasons))

    @staticmethod
    def _incident_text(incident: IncidentPayload, evidence: ParsedLogEvidence) -> str:
        return "\n".join(
            [
                incident.short_description,
                incident.description,
                incident.category,
                incident.raw_logs[:40_000],
                " ".join(incident.recent_changes),
                " ".join(evidence.error_signatures + evidence.status_codes),
            ]
        )

    def _vector_results(
        self, incident: IncidentPayload, evidence: ParsedLogEvidence, config: RuntimeConfig, limit: int
    ) -> tuple[list[tuple[dict[str, Any], float]], str, str]:
        embedder = EmbeddingClient(config)
        query = embedder.embed_many([self._incident_text(incident, evidence)])[0]
        if config.qdrant_url:
            store = QdrantVectorStore(config)
            seed_key = (config.qdrant_url, config.qdrant_collection, embedder.provider)
            if self._qdrant_seeded_for != seed_key:
                store.upsert(self._dataset, embedder.embed_many([case_text(case) for case in self._dataset]))
                self._qdrant_seeded_for = seed_key
            return store.search(query, limit), store.name, embedder.provider
        vectors = embedder.embed_many([case_text(case) for case in self._dataset])
        return LocalVectorStore().search(query, self._dataset, vectors, limit), LocalVectorStore.name, embedder.provider

    def find_matches(
        self, incident: IncidentPayload, evidence: ParsedLogEvidence, limit: int = 3
    ) -> tuple[HistoricalMatch, ...]:
        config = self._config_store.get()
        vector_results, backend, provider = self._vector_results(incident, evidence, config, limit)
        matches: list[HistoricalMatch] = []
        for case, vector_score in vector_results:
            heuristic = self._score_case(incident, evidence, case)
            normalized_vector = max(0.0, min(1.0, (vector_score + 1.0) / 2.0))
            combined = min(1.0, normalized_vector * 0.65 + heuristic.score * 0.35)
            reasons = (f"vector similarity: {normalized_vector:.1%} ({provider})", *heuristic.reasons)
            matches.append(HistoricalMatch(case, combined, reasons, backend))
        return tuple(sorted(matches, key=lambda item: item.score, reverse=True)[:limit])

    @staticmethod
    def _build_prompt(
        incident: IncidentPayload, evidence: ParsedLogEvidence, matches: tuple[HistoricalMatch, ...]
    ) -> str:
        examples = [
            {
                "input": {
                    "category": match.case["category"],
                    "description": match.case["description"],
                    "error_patterns": match.case["error_patterns"],
                },
                "verified_output": {
                    "root_cause": match.case["root_cause"],
                    "team": match.case["suggested_team"],
                    "actions": match.case["recommended_actions"],
                    "commands": match.case["verified_commands"],
                },
            }
            for match in matches
        ]
        target = {
            "incident": incident.model_dump(),
            "parsed_log_evidence": evidence.to_dict(),
        }
        return (
            "You are an incident RCA assistant. Use only the supplied incident evidence and historical "
            "examples. Treat examples as analogies, not proof. Return an RCAResult JSON object. Never invent "
            "observations. Prefer diagnostic commands and identify risky commands.\n\n"
            f"HISTORICAL_EXAMPLES:\n{json.dumps(examples, indent=2)}\n\n"
            f"INCIDENT_TO_ANALYZE:\n{json.dumps(target, indent=2)}"
        )

    @staticmethod
    def _evidence_lines(
        incident: IncidentPayload, evidence: ParsedLogEvidence, best: HistoricalMatch
    ) -> list[str]:
        lines = [f"Log signature detected: {item}" for item in evidence.error_signatures]
        lines.extend(f"Response status detected: {item}" for item in evidence.status_codes)
        lines.extend(f"Recent change: {change}" for change in incident.recent_changes[:5])
        lines.extend(f"Historical correlation: {reason}" for reason in best.reasons)
        if evidence.stack_traces:
            first_line = evidence.stack_traces[0].splitlines()[0]
            lines.append(f"Stack trace begins with: {first_line[:300]}")
        return lines or ["No strong structured log signature was found; assessment is based on textual similarity."]

    def analyze(self, incident: IncidentPayload, evidence: ParsedLogEvidence) -> RCAResult:
        matches = self.find_matches(incident, evidence)
        best = matches[0]
        confidence = max(0.20, min(0.97, 0.28 + best.score * 0.70))
        commands = [
            CommandDetail(
                command=item["command"],
                is_destructive=is_destructive_command(item["command"]),
                explanation=item["explanation"],
            )
            for item in best.case.get("verified_commands", [])
        ]
        result = RCAResult(
            incident_id=incident.incident_id,
            probable_root_cause=str(best.case["root_cause"]),
            confidence_score=round(confidence, 3),
            evidence_correlation=self._evidence_lines(incident, evidence, best),
            suggested_team=str(best.case["suggested_team"]),
            recommended_actions=list(best.case["recommended_actions"]),
            commands=commands,
        )
        prompt = self._build_prompt(incident, evidence, matches)
        config = self._config_store.get()
        if config.is_openai_ready:
            synthesized = OpenAIRCASynthesizer(config).synthesize(prompt)
            result = synthesized.model_copy(
                update={
                    "incident_id": incident.incident_id,
                    "commands": [
                        item.model_copy(update={"is_destructive": is_destructive_command(item.command)})
                        for item in synthesized.commands
                    ],
                }
            )
        context = IncidentContext(
            incident=incident,
            result=result,
            log_evidence=evidence,
            historical_matches=matches,
            system_prompt=prompt,
        )
        with self._lock:
            self._store[incident.incident_id] = context
        return result

    def get_context(self, incident_id: str) -> IncidentContext | None:
        with self._lock:
            return self._store.get(incident_id)

    def add_verified_resolution(self, case: dict[str, Any]) -> None:
        """Add a closed incident to the process-local retrieval corpus."""

        with self._lock:
            existing = next((item for item in self._dataset if item["incident_id"] == case["incident_id"]), None)
            if existing is not None:
                existing.update(case)
            else:
                self._dataset.append(case)
            config = self._config_store.get()
            if config.qdrant_url:
                embedder = EmbeddingClient(config)
                QdrantVectorStore(config).upsert([case], embedder.embed_many([case_text(case)]))

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            results = [context.result for context in self._store.values()]
            return {
                "incidents_analyzed": len(results),
                "knowledge_cases": len(self._dataset),
                "average_confidence": (
                    round(sum(item.confidence_score for item in results) / len(results), 3)
                    if results
                    else 0.0
                ),
                "high_confidence_incidents": sum(item.confidence_score >= 0.75 for item in results),
                "retrieval_backend": (
                    next(iter(self._store.values())).historical_matches[0].retrieval_backend
                    if self._store
                    else ("qdrant" if self._config_store.get().qdrant_url else "in-memory-vector")
                ),
                "embedding_provider": "openai" if self._config_store.get().is_openai_ready else "local-hashing",
            }
