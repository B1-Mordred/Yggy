from __future__ import annotations

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import ApprovalModel, AuditEventModel, TaskConfigVersionModel, TaskModel

from conftest import ADMIN_HEADERS, TOOL_HEADERS, sample_task


def test_admin_archive_hides_disabled_task_and_rejects_pending_approval(client):
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("archive_pending_task"))
    assert create_response.status_code == 201
    approval_id = create_response.json()["approval"]["id"]

    archive_response = client.post("/tasks/archive_pending_task/archive", headers=ADMIN_HEADERS)

    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"
    assert archive_response.json()["enabled"] is False

    listed = client.get("/tasks", headers=TOOL_HEADERS)
    assert listed.status_code == 200
    assert "archive_pending_task" not in {task["id"] for task in listed.json()}

    listed_with_archived = client.get("/tasks?include_archived=true", headers=ADMIN_HEADERS)
    assert listed_with_archived.status_code == 200
    assert "archive_pending_task" in {task["id"] for task in listed_with_archived.json()}

    with Session(get_engine()) as session:
        approval = session.get(ApprovalModel, approval_id)
        task = session.get(TaskModel, "archive_pending_task")
        version = (
            session.query(TaskConfigVersionModel)
            .filter(TaskConfigVersionModel.task_id == "archive_pending_task")
            .order_by(TaskConfigVersionModel.version.desc())
            .first()
        )
        audit = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.action == "task.archive")
            .filter(AuditEventModel.resource_id == "archive_pending_task")
            .first()
        )
        assert approval is not None
        assert approval.status == "rejected"
        assert task is not None
        assert task.status == "archived"
        assert task.config["enabled"] is False
        assert version is not None
        assert version.change_type == "archive"
        assert audit is not None
        assert audit.detail["rejected_pending_approvals"] == [approval_id]


def test_tool_key_cannot_archive_task(client):
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("archive_tool_denied"))
    assert create_response.status_code == 201

    response = client.post("/tasks/archive_tool_denied/archive", headers=TOOL_HEADERS)

    assert response.status_code == 403


def test_archive_rejects_enabled_task(client):
    task_id = "archive_enabled_task"
    create_response = client.post("/tasks/draft", headers=ADMIN_HEADERS, json=sample_task(task_id, "L0_READ_ONLY"))
    assert create_response.status_code == 201
    enabled = sample_task(task_id, "L0_READ_ONLY", enabled=True)
    update_response = client.put(f"/tasks/{task_id}", headers=ADMIN_HEADERS, json=enabled)
    assert update_response.status_code == 200

    response = client.post(f"/tasks/{task_id}/archive", headers=ADMIN_HEADERS)

    assert response.status_code == 409
    assert "paused before archive" in response.text
