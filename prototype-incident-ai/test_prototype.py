import pytest
from fastapi.testclient import TestClient

from integrations import ConfigStore, RuntimeConfig
from llm_synthesis import ClaudeAPIError, ClaudeRCASynthesizer
from log_parser import parse_logs
from main import app
from rca_engine import RCAEngine, is_destructive_command
from schemas import CommandDetail, IncidentPayload, RCAResult
from vector_store import VECTOR_SIZE, local_embedding


def test_ingest_dashboard_and_copilot() -> None:
    payload = {
        "incident_id": "INC-TEST",
        "short_description": "Java service OOM crash",
        "description": "Container restarted under load.",
        "category": "Application",
        "priority": "P1",
        "raw_logs": "java.lang.OutOfMemoryError: Java heap space\nHTTP 503",
        "recent_changes": ["New application release"],
    }
    with TestClient(app) as client:
        response = client.post("/api/v1/prototype/ingest", json=payload)
        assert response.status_code == 200
        assert response.json()["incident_id"] == "INC-TEST"
        assert response.json()["confidence_score"] > 0.5

        dashboard = client.get("/api/v1/prototype/dashboard/INC-TEST")
        assert dashboard.status_code == 200
        assert dashboard.json()["matching_historical_cases"][0]["incident_id"] == "HIST-1001"

        copilot = client.post(
            "/api/v1/prototype/copilot",
            json={"incident_id": "INC-TEST", "question": "What evidence supports the cause?"},
        )
        assert copilot.status_code == 200
        assert "Out of memory" in copilot.json()["answer"]


def test_destructive_command_detection() -> None:
    assert is_destructive_command("rm -rf /tmp/example")
    assert is_destructive_command("sudo systemctl stop checkout")
    assert is_destructive_command("kill -9 1234")
    assert not is_destructive_command("kubectl get pods")


def test_dashboard_config_and_servicenow_dry_run() -> None:
    webhook = {
        "sys_id": "0123456789abcdef0123456789abcdef",
        "number": "INC0099001",
        "short_description": "Filesystem full",
        "description": "Logging node cannot write new data.",
        "category": "Infrastructure",
        "priority": "2",
        "raw_logs": "No space left on device; disk usage 100%",
    }
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "ServiceNow instance URL" in page.text
        assert "Start every 2 minutes" in page.text

        scheduler = client.get("/api/v1/recurring-incidents")
        assert scheduler.status_code == 200
        assert scheduler.json()["active"] is False
        assert scheduler.json()["interval_seconds"] == 120

        config = client.put(
            "/api/v1/config",
            json={"anthropic_api_key": "test-secret", "write_back_enabled": False},
        )
        assert config.status_code == 200
        assert config.json()["anthropic_api_key_configured"] is True
        assert "test-secret" not in config.text

        response = client.post("/api/v1/servicenow/webhook", json=webhook)
        assert response.status_code == 200
        assert response.json()["rca"]["incident_id"] == "INC0099001"
        assert response.json()["write_back"]["status"] == "disabled"


def test_resolution_feedback_updates_knowledge_count() -> None:
    payload = {
        "sys_id": "feedback-sys-id",
        "incident_id": "INC-CLOSED-1",
        "short_description": "Verified DNS issue",
        "description": "Worker DNS resolution failed.",
        "category": "Network",
        "root_cause": "Stale resolver configuration",
        "resolution_notes": "Applied the approved resolver configuration.",
        "assignment_group": "Network Operations Team",
    }
    with TestClient(app) as client:
        before = client.get("/api/v1/metrics").json()["knowledge_cases"]
        response = client.post("/api/v1/servicenow/resolution-feedback", json=payload)
        assert response.status_code == 200
        assert response.json()["knowledge_cases"] == before + 1


def test_validated_server_cases_are_loaded_and_scheduler_has_aligned_scenarios() -> None:
    with TestClient(app) as client:
        metrics = client.get("/api/v1/metrics").json()
        scheduler = client.get("/api/v1/recurring-incidents").json()
        assert metrics["knowledge_cases"] == 25
        assert scheduler["scenario_count"] == 20
        assert scheduler["next_scenario_index"] == 1


def test_engineer_review_is_recorded_without_knowledge_promotion() -> None:
    incident = {
        "incident_id": "INC-REVIEW-1",
        "short_description": "RHEL /var filesystem is full",
        "description": "Application logging cannot continue.",
        "category": "Infrastructure",
        "priority": "P2",
        "raw_logs": "No space left on device; /var 98% used",
        "recent_changes": ["Debug logging enabled"],
    }
    review = {
        "decision": "needs_evidence",
        "reviewer": "engineer@example.test",
        "notes": "Confirm deleted-open files and retention requirements.",
        "validated_root_cause": "",
    }
    with TestClient(app) as client:
        assert client.post("/api/v1/prototype/ingest", json=incident).status_code == 200
        before = client.get("/api/v1/metrics").json()["knowledge_cases"]
        response = client.post("/api/v1/prototype/reviews/INC-REVIEW-1", json=review)
        assert response.status_code == 200
        assert response.json()["promoted_to_knowledge"] is False
        assert response.json()["review"]["decision"] == "needs_evidence"
        assert client.get("/api/v1/metrics").json()["knowledge_cases"] == before
        detail = client.get("/api/v1/prototype/dashboard/INC-REVIEW-1").json()
        assert detail["engineer_review"]["reviewer"] == "engineer@example.test"


def test_novel_incident_has_safe_low_confidence_path() -> None:
    payload = {
        "incident_id": "INC-NOVEL",
        "short_description": "Unclassified subsystem anomaly",
        "description": "An unfamiliar component produced an unknown condition.",
        "category": "Other",
        "priority": "P3",
        "raw_logs": "opaque-signal-98421",
        "recent_changes": [],
    }
    with TestClient(app) as client:
        response = client.post("/api/v1/prototype/ingest", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["confidence_score"] <= 0.45
        assert "Insufficient verified evidence" in result["probable_root_cause"]
        assert result["commands"] == []
        assert any("Historical correlation" in item for item in result["evidence_correlation"])


def test_no_match_removes_claude_invented_commands_and_caps_confidence(monkeypatch) -> None:
    store = ConfigStore()
    store.update({"anthropic_api_key": "sk-ant-test-key-long-enough"})
    engine = RCAEngine(config_store=store)
    incident = IncidentPayload(
        incident_id="INC-NOVEL",
        short_description="Badge controller rejects employee cards",
        description="A physical access controller became unreachable after a firmware change.",
        category="Physical Security",
        priority="P2",
        raw_logs="ACU-914 reader bus timeout hardware_controller_offline",
        recent_changes=["Controller firmware changed to 7.4.2"],
    )
    claude_result = RCAResult(
        incident_id="INC-NOVEL",
        probable_root_cause="Firmware 7.4.2 caused the controller failure.",
        confidence_score=0.99,
        evidence_correlation=["Controller became unreachable"],
        suggested_team="Physical Security",
        recommended_actions=["Collect controller diagnostics"],
        commands=[
            CommandDetail(
                command="ssh admin@ACU-914.local 'firmware rollback'",
                is_destructive=False,
                explanation="Rollback the controller firmware.",
            )
        ],
    )
    monkeypatch.setattr(
        "rca_engine.ClaudeRCASynthesizer.synthesize", lambda self, prompt: claude_result
    )

    result = engine.analyze(incident, parse_logs(incident.raw_logs))

    assert result.confidence_score == 0.45
    assert result.commands == []
    assert result.probable_root_cause.startswith("Unverified hypothesis:")
    assert any("no engineer-verified case" in item for item in result.evidence_correlation)


def test_local_embedding_is_deterministic_and_384_dimensional() -> None:
    first = local_embedding("OOMKilled after checkout deployment")
    second = local_embedding("OOMKilled after checkout deployment")
    assert first == second
    assert len(first) == VECTOR_SIZE


def test_claude_tool_output_is_validated(monkeypatch) -> None:
    class FakeResponse:
        is_error = False
        headers: dict[str, str] = {}

        def json(self) -> dict[str, object]:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "submit_incident_rca",
                        "input": {
                            "incident_id": "INC-CLAUDE",
                            "probable_root_cause": "Verified memory pressure",
                            "confidence_score": 0.8,
                            "evidence_correlation": ["OOMKilled"],
                            "suggested_team": "Application Platform Team",
                            "recommended_actions": ["Inspect the memory limit"],
                            "commands": [],
                        },
                    }
                ]
            }

    monkeypatch.setattr("llm_synthesis.httpx.post", lambda *args, **kwargs: FakeResponse())
    config = RuntimeConfig(anthropic_api_key="sk-ant-test-key-long-enough")
    result = ClaudeRCASynthesizer(config).synthesize("test prompt")
    assert result.incident_id == "INC-CLAUDE"
    assert result.confidence_score == 0.8


def test_claude_error_includes_provider_detail_without_credentials(monkeypatch) -> None:
    class FakeResponse:
        is_error = True
        status_code = 400
        reason_phrase = "Bad Request"
        headers = {"request-id": "req_test"}

        def json(self) -> dict[str, object]:
            return {"error": {"type": "invalid_request_error", "message": "Example provider detail"}}

    monkeypatch.setattr("llm_synthesis.httpx.post", lambda *args, **kwargs: FakeResponse())
    config = RuntimeConfig(anthropic_api_key="sk-ant-test-key-long-enough")
    with pytest.raises(ClaudeAPIError, match=r"HTTP 400: Example provider detail .*req_test"):
        ClaudeRCASynthesizer(config).synthesize("test prompt")


def test_claude_connection_check_returns_model_and_usage(monkeypatch) -> None:
    class FakeResponse:
        is_error = False
        headers: dict[str, str] = {}

        def json(self) -> dict[str, object]:
            return {
                "id": "msg_test",
                "model": "claude-haiku-4-5-20251001",
                "usage": {"input_tokens": 8, "output_tokens": 1},
            }

    monkeypatch.setattr("llm_synthesis.httpx.post", lambda *args, **kwargs: FakeResponse())
    config = RuntimeConfig(anthropic_api_key="sk-ant-test-key-long-enough")
    result = ClaudeRCASynthesizer(config).test_connection()
    assert result == {
        "connected": True,
        "model": "claude-haiku-4-5-20251001",
        "request_id": "msg_test",
        "input_tokens": 8,
        "output_tokens": 1,
    }
