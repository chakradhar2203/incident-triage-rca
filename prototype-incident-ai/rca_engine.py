"""Vector retrieval and grounded RCA synthesis."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from integrations import ConfigStore, RuntimeConfig
from llm_synthesis import ClaudeRCASynthesizer
from log_parser import ParsedLogEvidence
from schemas import CommandDetail, IncidentPayload, RCAResult
from vector_store import EmbeddingClient, FirestoreVectorStore, LocalVectorStore, case_text


_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}", re.I)
_DESTRUCTIVE = re.compile(
    r"(?:^|\s)(?:rm(?:\s|$)|kill\s+-9(?:\s|$)|drop\s+(?:database|table|schema)|systemctl\s+stop(?:\s|$)|reboot(?:\s|$))",
    re.I,
)
_STOP_WORDS = {"the", "and", "for", "with", "from", "that", "this", "after", "into", "its", "are", "was"}
_MINIMUM_MATCH_SCORE = 0.52
_NO_MATCH_CONFIDENCE_CAP = 0.45


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
        sources = (
            [dataset_path]
            if dataset_path is not None
            else [
                Path(__file__).with_name("mock_dataset.json"),
                Path(__file__).with_name("knowledge_cases_candidates.json"),
            ]
        )
        loaded: list[dict[str, Any]] = []
        for source in sources:
            with source.open("r", encoding="utf-8") as dataset_file:
                batch: object = json.load(dataset_file)
            if not isinstance(batch, list):
                raise ValueError(f"Historical incident dataset must be a JSON array: {source.name}")
            loaded.extend(item for item in batch if isinstance(item, dict))
        if not loaded:
            raise ValueError("Historical incident dataset must be a non-empty JSON array")
        identifiers = [str(item.get("incident_id", "")) for item in loaded]
        if any(not identifier for identifier in identifiers) or len(identifiers) != len(set(identifiers)):
            raise ValueError("Knowledge cases must have non-empty, unique incident_id values")
        self._dataset = loaded
        self._store: dict[str, IncidentContext] = {}
        self._reviews: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        self._config_store = config_store or ConfigStore()

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
        if config.is_firestore_ready:
            store = FirestoreVectorStore(config)
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
        ranked = sorted(matches, key=lambda item: item.score, reverse=True)
        return tuple(item for item in ranked if item.score >= _MINIMUM_MATCH_SCORE)[:limit]

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
        history_instruction = (
            "No verified historical match was found. Explicitly label the root cause as an unverified hypothesis, "
            "keep confidence at or below 0.45, return an empty commands array, and use only current evidence. "
            "Request more diagnostics where needed; never invent a host name, vendor CLI, or executable command."
            if not matches
            else "Treat historical examples as analogies, not proof. Commands may only be copied verbatim from "
            "verified_output.commands in the supplied historical examples."
        )
        return (
            "You are an incident RCA assistant. Use only the supplied incident evidence and historical "
            f"examples. {history_instruction} Return an RCAResult JSON object. Never invent "
            "observations. Prefer diagnostic commands and identify risky commands.\n\n"
            f"HISTORICAL_EXAMPLES:\n{json.dumps(examples, indent=2)}\n\n"
            f"INCIDENT_TO_ANALYZE:\n{json.dumps(target, indent=2)}"
        )

    @staticmethod
    def _evidence_lines(
        incident: IncidentPayload, evidence: ParsedLogEvidence, best: HistoricalMatch | None
    ) -> list[str]:
        lines = [f"Log signature detected: {item}" for item in evidence.error_signatures]
        lines.extend(f"Response status detected: {item}" for item in evidence.status_codes)
        lines.extend(f"Recent change: {change}" for change in incident.recent_changes[:5])
        if best is None:
            lines.append("Historical correlation: no verified case exceeded the similarity threshold.")
        else:
            lines.extend(f"Historical correlation: {reason}" for reason in best.reasons)
        if evidence.stack_traces:
            first_line = evidence.stack_traces[0].splitlines()[0]
            lines.append(f"Stack trace begins with: {first_line[:300]}")
        return lines or ["No strong structured log signature was found; assessment is based on textual similarity."]

    def analyze(self, incident: IncidentPayload, evidence: ParsedLogEvidence) -> RCAResult:
        matches = self.find_matches(incident, evidence)
        best = matches[0] if matches else None
        if best is None:
            result = RCAResult(
                incident_id=incident.incident_id,
                probable_root_cause="Insufficient verified evidence to identify a reliable root cause.",
                confidence_score=0.25,
                evidence_correlation=self._evidence_lines(incident, evidence, None),
                suggested_team=f"{incident.category} support team",
                recommended_actions=[
                    "Collect the component logs, dependency status, and resource metrics for the failure window.",
                    "Review recent deployments and configuration changes with the owning team.",
                    "Rerun the analysis after adding the missing evidence; do not execute corrective actions based only on this result.",
                ],
                commands=[],
            )
        else:
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
        if config.is_claude_ready:
            synthesized = ClaudeRCASynthesizer(config).synthesize(prompt)
            evidence_correlation = list(synthesized.evidence_correlation)
            if not matches:
                grounding_line = "Historical correlation: no engineer-verified case exceeded the similarity threshold."
                if not any("historical correlation" in item.casefold() for item in evidence_correlation):
                    evidence_correlation.append(grounding_line)
                probable_root_cause = synthesized.probable_root_cause
                if not probable_root_cause.casefold().startswith("unverified hypothesis"):
                    probable_root_cause = f"Unverified hypothesis: {probable_root_cause}"
                confidence_score = min(synthesized.confidence_score, _NO_MATCH_CONFIDENCE_CAP)
                grounded_commands: list[CommandDetail] = []
            else:
                assert best is not None
                grounding_line = (
                    f"Historical correlation: engineer-verified incident {best.case['incident_id']} matched at "
                    f"{best.score:.1%}."
                )
                if not any("historical correlation" in item.casefold() for item in evidence_correlation):
                    evidence_correlation.append(grounding_line)
                probable_root_cause = synthesized.probable_root_cause
                retrieval_confidence_cap = max(0.20, min(0.97, 0.28 + best.score * 0.70))
                confidence_score = min(synthesized.confidence_score, retrieval_confidence_cap)
                verified_commands = {
                    str(item["command"]): item
                    for match in matches
                    for item in match.case.get("verified_commands", [])
                    if isinstance(item, dict) and item.get("command") and item.get("explanation")
                }
                grounded_commands = [
                    CommandDetail(
                        command=item.command,
                        is_destructive=is_destructive_command(item.command),
                        explanation=str(verified_commands[item.command]["explanation"]),
                    )
                    for item in synthesized.commands
                    if item.command in verified_commands
                ]
            result = synthesized.model_copy(
                update={
                    "incident_id": incident.incident_id,
                    "probable_root_cause": probable_root_cause,
                    "confidence_score": confidence_score,
                    "evidence_correlation": evidence_correlation,
                    "commands": grounded_commands,
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

    def submit_review(self, incident_id: str, review: dict[str, Any]) -> dict[str, Any]:
        """Record an engineer decision without promoting it into verified knowledge."""

        with self._lock:
            if incident_id not in self._store:
                raise KeyError(incident_id)
            record = {
                **review,
                "incident_id": incident_id,
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
            self._reviews[incident_id] = record
            return dict(record)

    def get_review(self, incident_id: str) -> dict[str, Any] | None:
        with self._lock:
            review = self._reviews.get(incident_id)
            return dict(review) if review else None

    def add_verified_resolution(self, case: dict[str, Any]) -> None:
        """Add a closed incident to the process-local retrieval corpus."""

        with self._lock:
            existing = next((item for item in self._dataset if item["incident_id"] == case["incident_id"]), None)
            if existing is not None:
                existing.update(case)
            else:
                self._dataset.append(case)
            config = self._config_store.get()
        if config.is_firestore_ready:
            embedder = EmbeddingClient(config)
            FirestoreVectorStore(config).upsert([case], embedder.embed_many([case_text(case)]))

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
                "pending_engineer_reviews": sum(
                    incident_id not in self._reviews for incident_id in self._store
                ),
                "retrieval_backend": self._retrieval_backend_name(),
                "embedding_provider": "local-hashing-v1",
                "generation_provider": "claude" if self._config_store.get().is_claude_ready else "deterministic-local",
            }

    def _retrieval_backend_name(self) -> str:
        if self._config_store.get().is_firestore_ready:
            return FirestoreVectorStore.name
        if self._store:
            context = next(iter(self._store.values()))
            if context.historical_matches:
                return context.historical_matches[0].retrieval_backend
        return LocalVectorStore.name
