from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import AuditEventModel, RunModel, TaskModel, utcnow
from conftest import ADMIN_HEADERS, TOOL_HEADERS, sample_task


def test_run_request_reuses_active_run(client):
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("active_dedupe", "L0_READ_ONLY"))
    assert create_response.status_code == 201

    first = client.post("/tasks/active_dedupe/run", headers=TOOL_HEADERS)
    second = client.post("/tasks/active_dedupe/run", headers=TOOL_HEADERS)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["deduplicated"] is True
    assert second.json()["reason"] == "active_run_exists"


def test_recent_live_completion_blocks_duplicate_tool_run(client):
    task = sample_task("recent_live_dedupe", "L1_NOTIFY_ONLY", enabled=True, runtime={"dry_run": False})
    completed_at = utcnow()
    with Session(get_engine()) as session:
        session.add(
            TaskModel(
                id=task["id"],
                name=task["name"],
                type=task["type"],
                enabled=True,
                owner=task["owner"],
                created_by=task["created_by"],
                approval_level=task["policy"]["approval_level"],
                status="enabled",
                config=task,
            )
        )
        session.add(
            RunModel(
                id=str(uuid.uuid4()),
                task_id=task["id"],
                status="completed",
                log={"message": "recent live run"},
                completed_at=completed_at,
            )
        )
        session.commit()

    response = client.post(f"/tasks/{task['id']}/run", headers=TOOL_HEADERS)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "duplicate_recent"
    assert body["deduplicated"] is True
    assert body["dedupe_seconds"] == 300


def test_admin_can_force_recent_live_duplicate(client):
    task = sample_task("force_recent_live", "L1_NOTIFY_ONLY", enabled=True, runtime={"dry_run": False})
    with Session(get_engine()) as session:
        session.add(
            TaskModel(
                id=task["id"],
                name=task["name"],
                type=task["type"],
                enabled=True,
                owner=task["owner"],
                created_by=task["created_by"],
                approval_level=task["policy"]["approval_level"],
                status="enabled",
                config=task,
            )
        )
        session.add(
            RunModel(
                id=str(uuid.uuid4()),
                task_id=task["id"],
                status="completed",
                log={"message": "recent live run"},
                completed_at=utcnow(),
            )
        )
        session.commit()

    tool_force = client.post(f"/tasks/{task['id']}/run", headers=TOOL_HEADERS, json={"force": True})
    admin_force = client.post(f"/tasks/{task['id']}/run", headers=ADMIN_HEADERS, json={"force": True})

    assert tool_force.status_code == 403
    assert admin_force.status_code == 202
    assert admin_force.json()["status"] == "queued"
    assert admin_force.json()["deduplicated"] is False


def test_min_seconds_between_runs_blocks_repeat_and_audits(client):
    task = sample_task("min_seconds_limit", "L0_READ_ONLY", policy={"min_seconds_between_runs": 600})
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert create_response.status_code == 201

    first = client.post("/tasks/min_seconds_limit/run", headers=TOOL_HEADERS)
    assert first.status_code == 202
    with Session(get_engine()) as session:
        run = session.get(RunModel, first.json()["run_id"])
        run.status = "completed_dry_run"
        run.completed_at = utcnow()
        session.commit()

    second = client.post("/tasks/min_seconds_limit/run", headers=TOOL_HEADERS)

    assert second.status_code == 202
    body = second.json()
    assert body["queued"] is False
    assert body["status"] == "rate_limited"
    assert body["reason"] == "min_seconds_between_runs"
    assert body["retry_after_seconds"] > 0
    with Session(get_engine()) as session:
        event = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.action == "task.run.denied")
            .filter(AuditEventModel.resource_id == "min_seconds_limit")
            .one()
        )
        assert event.detail["reason"] == "min_seconds_between_runs"


def test_hourly_run_limit_blocks_queueing(client):
    task = sample_task("hourly_limit", "L0_READ_ONLY", policy={"max_runs_per_hour": 2})
    with Session(get_engine()) as session:
        session.add(
            TaskModel(
                id=task["id"],
                name=task["name"],
                type=task["type"],
                enabled=False,
                owner=task["owner"],
                created_by=task["created_by"],
                approval_level=task["policy"]["approval_level"],
                status="draft",
                config=task,
            )
        )
        session.add_all(
            [
                RunModel(
                    id=str(uuid.uuid4()),
                    task_id=task["id"],
                    status="completed_dry_run",
                    log={"message": "recent dry run"},
                    created_at=utcnow() - timedelta(minutes=10),
                    completed_at=utcnow() - timedelta(minutes=9),
                ),
                RunModel(
                    id=str(uuid.uuid4()),
                    task_id=task["id"],
                    status="completed_dry_run",
                    log={"message": "recent dry run"},
                    created_at=utcnow() - timedelta(minutes=5),
                    completed_at=utcnow() - timedelta(minutes=4),
                ),
            ]
        )
        session.commit()

    response = client.post(f"/tasks/{task['id']}/run", headers=TOOL_HEADERS)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "rate_limited"
    assert body["reason"] == "max_runs_per_hour"
    assert body["current_count"] == 2


def test_stale_active_run_is_recovered_before_queueing(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_RUN_LEASE_SECONDS", "600")
    task = sample_task("stale_before_queue", "L0_READ_ONLY")
    old_run_id = str(uuid.uuid4())
    with Session(get_engine()) as session:
        session.add(
            TaskModel(
                id=task["id"],
                name=task["name"],
                type=task["type"],
                enabled=False,
                owner=task["owner"],
                created_by=task["created_by"],
                approval_level=task["policy"]["approval_level"],
                status="draft",
                config=task,
            )
        )
        session.add(
            RunModel(
                id=old_run_id,
                task_id=task["id"],
                status="running_dry_run",
                log={"message": "old running task"},
                created_at=utcnow() - timedelta(hours=2),
            )
        )
        session.commit()

    response = client.post(f"/tasks/{task['id']}/run", headers=TOOL_HEADERS)

    assert response.status_code == 202
    body = response.json()
    assert body["queued"] is True
    assert body["deduplicated"] is False
    assert body["run_id"] != old_run_id
    with Session(get_engine()) as session:
        stale_run = session.get(RunModel, old_run_id)
        assert stale_run.status == "failed_stale_dry_run"
        assert stale_run.completed_at is not None
        assert stale_run.log["stale_recovery"]["previous_status"] == "running_dry_run"
