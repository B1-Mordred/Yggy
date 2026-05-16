from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import RunModel
from conftest import TOOL_HEADERS, WORKER_HEADERS


def test_run_logs_redact_secret_values(client):
    run_id = str(uuid.uuid4())
    with Session(get_engine()) as session:
        session.add(
            RunModel(
                id=run_id,
                task_id="redaction_task",
                status="completed",
                log={"message": "ok", "api_token": "super-secret-value", "nested": {"password": "hunter2"}},
            )
        )
        session.commit()

    response = client.get(f"/runs/{run_id}", headers=TOOL_HEADERS)
    assert response.status_code == 200
    log = response.json()["log"]
    assert log["api_token"] == "[REDACTED]"
    assert log["nested"]["password"] == "[REDACTED]"


def test_worker_can_complete_run_with_redacted_log(client):
    run_id = str(uuid.uuid4())
    with Session(get_engine()) as session:
        session.add(RunModel(id=run_id, task_id="worker_task", status="queued", log={"message": "queued"}))
        session.commit()

    response = client.patch(
        f"/runs/{run_id}",
        headers=WORKER_HEADERS,
        json={"status": "completed", "log": {"message": "ok", "discord_token": "secret"}, "completed": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None
    assert body["log"]["discord_token"] == "[REDACTED]"


def test_worker_can_list_queued_runs(client):
    run_id = str(uuid.uuid4())
    with Session(get_engine()) as session:
        session.add(RunModel(id=run_id, task_id="worker_task", status="queued", log={"message": "queued"}))
        session.commit()

    response = client.get("/runs", headers=WORKER_HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["id"] == run_id


def test_run_list_filters_by_task_status_and_limit(client):
    with Session(get_engine()) as session:
        session.add_all(
            [
                RunModel(id=str(uuid.uuid4()), task_id="daily", status="completed", log={"message": "ok"}),
                RunModel(id=str(uuid.uuid4()), task_id="daily", status="failed", log={"message": "bad"}),
                RunModel(id=str(uuid.uuid4()), task_id="other", status="failed", log={"message": "other"}),
            ]
        )
        session.commit()

    response = client.get("/runs?task_id=daily&status=failed&limit=1", headers=TOOL_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["task_id"] == "daily"
    assert body[0]["status"] == "failed"


def test_worker_claims_queued_run_once(client):
    run_id = str(uuid.uuid4())
    with Session(get_engine()) as session:
        session.add(RunModel(id=run_id, task_id="worker_task", status="queued", log={"message": "queued"}))
        session.commit()

    first = client.post(f"/runs/{run_id}/claim", headers=WORKER_HEADERS)
    second = client.post(f"/runs/{run_id}/claim", headers=WORKER_HEADERS)

    assert first.status_code == 200
    assert first.json()["status"] == "running"
    assert first.json()["dry_run"] is False
    assert second.status_code == 409


def test_worker_claim_preserves_dry_run_status(client):
    run_id = str(uuid.uuid4())
    with Session(get_engine()) as session:
        session.add(RunModel(id=run_id, task_id="worker_task", status="queued_dry_run", log={"message": "queued"}))
        session.commit()

    response = client.post(f"/runs/{run_id}/claim", headers=WORKER_HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == "running_dry_run"
    assert response.json()["dry_run"] is True
