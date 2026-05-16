from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import ApprovalModel, RunModel, TaskModel, utcnow
from conftest import ADMIN_HEADERS, TOOL_HEADERS, sample_task


def test_ops_dashboard_requires_basic_credentials(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")

    denied = client.get("/ops")
    allowed = client.get("/ops", auth=("operator", "test-dashboard-password"))

    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Basic"
    assert allowed.status_code == 200
    assert "Yggy Operations" in allowed.text


def test_admin_key_can_access_ops_status_without_dashboard_password(client):
    response = client.get("/ops/status", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["safety"]["read_only"] is True
    assert body["safety"]["openapi_exposed"] is False


def test_tool_key_cannot_access_ops_status(client):
    response = client.get("/ops/status", headers=TOOL_HEADERS)

    assert response.status_code in {401, 503}


def test_ops_status_summarizes_without_logs_or_nonces(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task_id = "daily_local_ai_security_briefing"
    run_id = str(uuid.uuid4())
    config = sample_task(task_id)
    with Session(get_engine()) as session:
        session.add(
            TaskModel(
                id=task_id,
                name="Daily Local AI Security Briefing",
                type="topic_digest",
                enabled=True,
                owner="local_user",
                created_by="yggdrasil",
                approval_level="L1_NOTIFY_ONLY",
                status="enabled",
                config={**config, "enabled": True},
            )
        )
        session.flush()
        session.add_all(
            [
                RunModel(
                    id=run_id,
                    task_id=task_id,
                    status="completed",
                    log={
                        "result": {"status": "ready", "items": [{"title": "Item"}]},
                        "notification": {"sent": True, "target": "briefings", "transport": "bot"},
                        "api_token": "super-secret-value",
                    },
                    created_at=utcnow(),
                    completed_at=utcnow(),
                ),
                ApprovalModel(
                    id="approval-ops-test",
                    task_id=task_id,
                    approval_level="L1_NOTIFY_ONLY",
                    requested_by="yggdrasil",
                    status="pending",
                    summary="Approve daily briefing",
                    risk="low",
                    nonce_hash="nonce-hash-secret",
                    created_at=utcnow(),
                ),
            ]
        )
        session.commit()

    response = client.get("/ops/status", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["tasks"] == 1
    assert body["counts"]["enabled_tasks"] == 1
    assert body["counts"]["pending_approvals"] == 1
    assert body["tasks"][0]["latest_run"]["id"] == run_id
    assert body["recent_runs"][0]["notification"]["sent"] is True
    assert "nonce" not in response.text.lower()
    assert "super-secret-value" not in response.text


def test_ops_routes_are_not_in_openapi(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/ops" not in paths
    assert "/ops/status" not in paths
