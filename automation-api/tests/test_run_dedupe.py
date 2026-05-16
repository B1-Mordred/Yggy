from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import RunModel, TaskModel, utcnow
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
