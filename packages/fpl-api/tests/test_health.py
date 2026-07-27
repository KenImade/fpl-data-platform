from datetime import UTC, datetime

from fastapi.testclient import TestClient
from fpl_api.main import VERSION, app

client = TestClient(app)


def test_health_endpoint_returns_ok_status(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc123")

    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert data["version"] == VERSION
    assert data["sha"] == "abc123"

    timestamp = datetime.fromisoformat(data["timestamp"])
    assert timestamp.tzinfo == UTC
