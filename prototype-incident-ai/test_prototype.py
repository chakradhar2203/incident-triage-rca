from fastapi.testclient import TestClient

from main import app
from rca_engine import is_destructive_command


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
            json={"openai_api_key": "test-secret", "write_back_enabled": False},
        )
        assert config.status_code == 200
        assert config.json()["openai_api_key_configured"] is True
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
