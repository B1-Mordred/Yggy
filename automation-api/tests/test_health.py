from __future__ import annotations

from conftest import TOOL_HEADERS, WORKER_HEADERS


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"]["connected"] is True


def test_worker_heartbeat_updates_health(client):
    update = client.post(
        "/health/heartbeat",
        headers=WORKER_HEADERS,
        json={"service": "automation-worker", "status": "ok", "detail": {"event": "poll", "token": "secret"}},
    )
    health = client.get("/health", headers=TOOL_HEADERS)

    assert update.status_code == 200
    assert update.json()["ok"] is True
    body = health.json()
    assert body["worker"]["ok"] is True
    assert body["worker"]["status"] == "ok"
    assert body["worker"]["detail"]["token"] == "[REDACTED]"
