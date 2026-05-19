from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import ApprovalModel, AuditEventModel, RunModel, TaskConfigVersionModel, TaskModel, utcnow
from app.services.task_version_service import record_task_config_version
from conftest import ADMIN_HEADERS, TOOL_HEADERS, WORKER_HEADERS, sample_task


def test_tool_key_cannot_run_retention(client):
    response = client.post("/maintenance/retention", headers=TOOL_HEADERS)

    assert response.status_code == 403


def test_worker_can_preview_retention_without_deleting(client):
    old = utcnow() - timedelta(days=45)
    run_id = str(uuid.uuid4())
    with Session(get_engine()) as session:
        session.add(
            RunModel(
                id=run_id,
                task_id="daily_local_ai_security_briefing",
                status="completed",
                log={"message": "old"},
                created_at=old,
                completed_at=old,
            )
        )
        session.commit()

    response = client.post(
        "/maintenance/retention",
        headers=WORKER_HEADERS,
        json={"dry_run": True, "run_retention_days": 30},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["matched"]["runs"] == 1
    assert body["deleted"]["runs"] == 0

    with Session(get_engine()) as session:
        assert session.get(RunModel, run_id) is not None


def test_admin_retention_deletes_old_runs_audits_and_temporary_tasks(client):
    old_run_at = utcnow() - timedelta(days=45)
    old_audit_at = utcnow() - timedelta(days=120)
    old_temp_at = utcnow() - timedelta(hours=48)
    old_run_id = str(uuid.uuid4())
    new_run_id = str(uuid.uuid4())
    active_old_run_id = str(uuid.uuid4())
    temp_task_id = "temporary_cleanup_test_task"
    protected_task_id = "daily_local_ai_security_briefing"
    temp_config = sample_task(temp_task_id)
    protected_config = sample_task(protected_task_id)

    with Session(get_engine()) as session:
        session.add_all(
            [
                TaskModel(
                    id=temp_task_id,
                    name="Temporary Cleanup Test Task",
                    type="topic_digest",
                    enabled=False,
                    owner="local_user",
                    created_by="local_admin",
                    approval_level="L1_NOTIFY_ONLY",
                    status="paused",
                    config=temp_config,
                    created_at=old_temp_at,
                    updated_at=old_temp_at,
                ),
                TaskModel(
                    id=protected_task_id,
                    name="Daily Local AI Security Briefing",
                    type="topic_digest",
                    enabled=False,
                    owner="local_user",
                    created_by="yggdrasil",
                    approval_level="L1_NOTIFY_ONLY",
                    status="paused",
                    config=protected_config,
                    created_at=old_temp_at,
                    updated_at=old_temp_at,
                ),
            ]
        )
        session.flush()
        temp_task = session.get(TaskModel, temp_task_id)
        assert temp_task is not None
        record_task_config_version(
            session,
            temp_task,
            actor_role="tool",
            change_type="draft",
            summary="Temporary task version for retention cleanup.",
        )
        session.add_all(
            [
                RunModel(
                    id=old_run_id,
                    task_id=protected_task_id,
                    status="completed",
                    log={"message": "old"},
                    created_at=old_run_at,
                    completed_at=old_run_at,
                ),
                RunModel(
                    id=new_run_id,
                    task_id=protected_task_id,
                    status="completed",
                    log={"message": "new"},
                    created_at=utcnow(),
                    completed_at=utcnow(),
                ),
                RunModel(
                    id=active_old_run_id,
                    task_id=protected_task_id,
                    status="running",
                    log={"message": "active"},
                    created_at=old_run_at,
                    completed_at=None,
                ),
                AuditEventModel(
                    actor_role="worker",
                    action="old.action",
                    resource_type="run",
                    resource_id=old_run_id,
                    detail={},
                    created_at=old_audit_at,
                ),
                ApprovalModel(
                    id="approval-temp-cleanup",
                    task_id=temp_task_id,
                    approval_level="L1_NOTIFY_ONLY",
                    requested_by="local_admin",
                    status="approved",
                    summary="temporary cleanup approval",
                    risk="low",
                    nonce_hash="hash",
                    created_at=old_temp_at,
                    decided_at=old_temp_at,
                ),
            ]
        )
        session.commit()

    response = client.post(
        "/maintenance/retention",
        headers=ADMIN_HEADERS,
        json={"run_retention_days": 30, "audit_retention_days": 90, "temp_task_retention_hours": 24},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"]["runs"] == 1
    assert body["deleted"]["audit_events"] == 1
    assert body["deleted"]["temporary_tasks"] == 1
    assert body["deleted"]["temporary_task_approvals"] == 1
    assert body["deleted"]["temporary_task_config_versions"] == 1
    assert body["temporary_task_ids"] == [temp_task_id]

    with Session(get_engine()) as session:
        assert session.get(RunModel, old_run_id) is None
        assert session.get(RunModel, new_run_id) is not None
        assert session.get(RunModel, active_old_run_id) is not None
        assert session.get(TaskModel, temp_task_id) is None
        assert session.get(TaskModel, protected_task_id) is not None
        assert session.get(ApprovalModel, "approval-temp-cleanup") is None
        assert session.query(TaskConfigVersionModel).filter(TaskConfigVersionModel.task_id == temp_task_id).count() == 0
        assert session.query(AuditEventModel).filter(AuditEventModel.action == "maintenance.retention.apply").count() == 1
