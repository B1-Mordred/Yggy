from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import ApprovalModel, AuditEventModel, RunModel, TaskModel, utcnow
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
    assert "data-view-target=\"audit\"" in allowed.text
    assert "data-view=\"tasks\"" in allowed.text


def test_admin_key_can_access_ops_status_without_dashboard_password(client):
    response = client.get("/ops/status", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["safety"]["read_only"] is False
    assert body["safety"]["approval_actions_enabled"] is True
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
    assert body["pending_approvals"][0]["review"]["actions"]
    assert body["pending_approvals"][0]["review"]["failure_mode"]
    assert body["pending_approvals"][0]["review"]["config_change"]["enabled_after_approval"] is True
    assert "nonce" not in response.text.lower()
    assert "super-secret-value" not in response.text


def test_ops_run_detail_shows_redacted_digest_n8n_and_discord_result(client, monkeypatch):
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
        session.add(
            RunModel(
                id=run_id,
                task_id=task_id,
                status="completed",
                log={
                    "result": {
                        "status": "ready",
                        "title": "Daily Local AI Security Briefing",
                        "message": "digest body",
                        "source_count": 4,
                        "summary_mode": "llm",
                        "items": [
                            {
                                "title": "Open WebUI release",
                                "summary": "Security-relevant local AI update.",
                                "link": "https://example.com/open-webui",
                                "published": "2026-05-17",
                                "type": "rss",
                            }
                        ],
                        "errors": [{"source": "https://example.com/feed.xml", "error": "Timeout"}],
                        "n8n": {
                            "status": "ready",
                            "notify": False,
                            "webhook_id": "daily_briefing_stub",
                            "path": "/webhook/yggy-daily-briefing",
                            "status_code": 200,
                            "message": "n8n webhook daily_briefing_stub dispatched.",
                            "response": {
                                "action": "normalize_digest_payload",
                                "normalized": {"item_count": 1, "source_count": 1},
                                "authorization": "Bearer hidden-secret",
                            },
                        },
                    },
                    "notification_decision": {
                        "send": True,
                        "reason": "enabled",
                        "classification": "success",
                        "secret_token": "hidden-secret",
                    },
                    "notification": {
                        "sent": True,
                        "dry_run": False,
                        "target": "briefings",
                        "transport": "bot",
                        "status_code": 200,
                        "discord_token": "hidden-secret",
                    },
                    "api_token": "hidden-secret",
                },
                created_at=utcnow(),
                completed_at=utcnow(),
            )
        )
        session.commit()

    response = client.get(f"/ops/runs/{run_id}", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["id"] == run_id
    assert body["task"]["id"] == task_id
    assert body["digest"]["item_count"] == 1
    assert body["digest"]["error_count"] == 1
    assert body["digest"]["summary_mode"] == "llm"
    assert body["digest"]["items"][0]["url"] == "https://example.com/open-webui"
    assert body["n8n"]["webhook_id"] == "daily_briefing_stub"
    assert body["n8n"]["response"]["action"] == "normalize_digest_payload"
    assert body["n8n"]["response"]["authorization"] == "[REDACTED]"
    assert body["notification_decision"]["send"] is True
    assert body["notification_decision"]["secret_token"] == "[REDACTED]"
    assert body["notification"]["sent"] is True
    assert body["notification"]["discord_token"] == "[REDACTED]"
    assert "hidden-secret" not in response.text
    assert "api_token" not in response.text


def test_ops_audit_events_are_redacted_and_limited(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    with Session(get_engine()) as session:
        session.add_all(
            [
                AuditEventModel(
                    actor_role="tool",
                    action="task.draft",
                    resource_type="task",
                    resource_id="older_task",
                    detail={"message": "older"},
                    created_at=utcnow(),
                ),
                AuditEventModel(
                    actor_role="ops_dashboard",
                    action="task.run",
                    resource_type="task",
                    resource_id="daily_local_ai_security_briefing",
                    detail={
                        "run_id": "run-1",
                        "dry_run": True,
                        "api_token": "hidden-secret",
                        "nested": {"authorization": "Bearer hidden-secret"},
                    },
                    created_at=utcnow(),
                ),
            ]
        )
        session.commit()

    response = client.get("/ops/audit?limit=1", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 1
    assert len(body["events"]) == 1
    event = body["events"][0]
    assert event["action"] == "task.run"
    assert event["resource_id"] == "daily_local_ai_security_briefing"
    assert event["detail"]["api_token"] == "[REDACTED]"
    assert event["detail"]["nested"]["authorization"] == "[REDACTED]"
    assert "hidden-secret" not in response.text


def test_ops_task_run_requires_action_header(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_run_header", enabled=True)
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
        session.commit()

    response = client.post(
        "/ops/tasks/ops_run_header/run",
        auth=("operator", "test-dashboard-password"),
        json={"mode": "dry_run"},
    )

    assert response.status_code == 403
    assert "missing ops run action header" in response.text


def test_ops_task_dry_run_queues_without_live_side_effects(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_dry_run_live_task", enabled=True, runtime={"dry_run": False})
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
        session.commit()

    response = client.post(
        f"/ops/tasks/{task['id']}/run",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "manual-run"},
        json={"mode": "dry_run"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued_dry_run"
    assert body["mode"] == "dry_run"
    with Session(get_engine()) as session:
        run = session.get(RunModel, body["run_id"])
        audit = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.resource_id == task["id"])
            .order_by(AuditEventModel.created_at.desc())
            .first()
        )
        assert run is not None
        assert run.status == "queued_dry_run"
        assert run.log["dry_run"] is True
        assert audit is not None
        assert audit.actor_role == "ops_dashboard"
        assert audit.action == "task.run"


def test_ops_task_live_run_queues_enabled_l1_with_live_override(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_live_run", enabled=True, runtime={"dry_run": True})
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
        session.commit()

    response = client.post(
        f"/ops/tasks/{task['id']}/run",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "manual-run"},
        json={"mode": "live"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["mode"] == "live"
    with Session(get_engine()) as session:
        run = session.get(RunModel, body["run_id"])
        assert run is not None
        assert run.status == "queued"
        assert run.log["dry_run"] is False


def test_ops_task_live_run_rejects_disabled_and_l2_tasks(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    disabled = sample_task("ops_live_disabled", enabled=False, runtime={"dry_run": False})
    l2 = sample_task("ops_live_l2", "L2_LOCAL_WRITE", enabled=True, runtime={"dry_run": False})
    with Session(get_engine()) as session:
        for task in (disabled, l2):
            session.add(
                TaskModel(
                    id=task["id"],
                    name=task["name"],
                    type=task["type"],
                    enabled=task["enabled"],
                    owner=task["owner"],
                    created_by=task["created_by"],
                    approval_level=task["policy"]["approval_level"],
                    status="enabled" if task["enabled"] else "draft",
                    config=task,
                )
            )
        session.commit()

    disabled_response = client.post(
        "/ops/tasks/ops_live_disabled/run",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "manual-run"},
        json={"mode": "live"},
    )
    l2_response = client.post(
        "/ops/tasks/ops_live_l2/run",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "manual-run"},
        json={"mode": "live"},
    )

    assert disabled_response.status_code == 403
    assert "enabled" in disabled_response.text
    assert l2_response.status_code == 403
    assert "L2+" in l2_response.text


def test_ops_task_live_run_preserves_recent_completion_dedupe(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_recent_live_dedupe", enabled=True, runtime={"dry_run": False})
    recent_run_id = str(uuid.uuid4())
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
                id=recent_run_id,
                task_id=task["id"],
                status="completed",
                log={"message": "recent live run"},
                completed_at=utcnow(),
            )
        )
        session.commit()

    response = client.post(
        f"/ops/tasks/{task['id']}/run",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "manual-run"},
        json={"mode": "live"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["run_id"] == recent_run_id
    assert body["status"] == "duplicate_recent"
    assert body["deduplicated"] is True
    assert body["reason"] == "recent_completed_run"


def test_ops_task_pause_requires_action_header(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_pause_header", enabled=True)
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
        session.commit()

    response = client.post("/ops/tasks/ops_pause_header/pause", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 403
    assert "missing ops task state action header" in response.text


def test_ops_task_pause_updates_task_state_and_config(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_pause_task", enabled=True)
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
        session.commit()

    response = client.post(
        "/ops/tasks/ops_pause_task/pause",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-state"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["status"] == "paused"
    with Session(get_engine()) as session:
        task_model = session.get(TaskModel, "ops_pause_task")
        audit = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.resource_id == "ops_pause_task")
            .order_by(AuditEventModel.created_at.desc())
            .first()
        )
        assert task_model is not None
        assert task_model.enabled is False
        assert task_model.status == "paused"
        assert task_model.config["enabled"] is False
        assert audit is not None
        assert audit.actor_role == "ops_dashboard"
        assert audit.action == "task.pause"


def test_ops_task_resume_requires_l1_approved_approval(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_resume_unapproved", enabled=False)
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
                status="paused",
                config=task,
            )
        )
        session.commit()

    response = client.post(
        "/ops/tasks/ops_resume_unapproved/resume",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-state"},
    )

    assert response.status_code == 403
    assert "approved L1 task required" in response.text


def test_ops_task_resume_reenables_approved_l1_task(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_resume_approved", enabled=False)
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
                status="paused",
                config=task,
            )
        )
        session.flush()
        session.add(
            ApprovalModel(
                id="approval-resume-approved",
                task_id=task["id"],
                approval_level=task["policy"]["approval_level"],
                requested_by="yggdrasil",
                status="approved",
                summary="Approved before pause",
                risk=task["policy"]["approval_level"],
                nonce_hash="nonce-hash",
                created_at=utcnow(),
                decided_at=utcnow(),
            )
        )
        session.commit()

    response = client.post(
        "/ops/tasks/ops_resume_approved/resume",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-state"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["status"] == "enabled"
    with Session(get_engine()) as session:
        task_model = session.get(TaskModel, "ops_resume_approved")
        audit = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.resource_id == "ops_resume_approved")
            .order_by(AuditEventModel.created_at.desc())
            .first()
        )
        assert task_model is not None
        assert task_model.enabled is True
        assert task_model.status == "enabled"
        assert task_model.config["enabled"] is True
        assert audit is not None
        assert audit.actor_role == "ops_dashboard"
        assert audit.action == "task.resume"


def test_ops_task_resume_rejects_pending_rejected_and_l2_tasks(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    pending = sample_task("ops_resume_pending", enabled=False)
    rejected = sample_task("ops_resume_rejected", enabled=False)
    l2 = sample_task("ops_resume_l2", "L2_LOCAL_WRITE", enabled=False)
    with Session(get_engine()) as session:
        for task, status_value in ((pending, "pending_approval"), (rejected, "rejected"), (l2, "paused")):
            session.add(
                TaskModel(
                    id=task["id"],
                    name=task["name"],
                    type=task["type"],
                    enabled=False,
                    owner=task["owner"],
                    created_by=task["created_by"],
                    approval_level=task["policy"]["approval_level"],
                    status=status_value,
                    config=task,
                )
            )
        session.commit()

    pending_response = client.post(
        "/ops/tasks/ops_resume_pending/resume",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-state"},
    )
    rejected_response = client.post(
        "/ops/tasks/ops_resume_rejected/resume",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-state"},
    )
    l2_resume_response = client.post(
        "/ops/tasks/ops_resume_l2/resume",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-state"},
    )
    l2_pause_response = client.post(
        "/ops/tasks/ops_resume_l2/pause",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-state"},
    )

    assert pending_response.status_code == 403
    assert "pending approval" in pending_response.text
    assert rejected_response.status_code == 403
    assert "new approval" in rejected_response.text
    assert l2_resume_response.status_code == 403
    assert "resume L2+" in l2_resume_response.text
    assert l2_pause_response.status_code == 403
    assert "pause L2+" in l2_pause_response.text


def test_ops_task_resume_allows_l0_without_approval(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_resume_l0", "L0_READ_ONLY", enabled=False)
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
                status="paused",
                config=task,
            )
        )
        session.commit()

    response = client.post(
        "/ops/tasks/ops_resume_l0/resume",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-state"},
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_ops_approval_requires_action_header(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")

    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("ops_header_check"))
    approval = create_response.json()["approval"]

    response = client.post(
        f"/ops/approvals/{approval['id']}/approve",
        auth=("operator", "test-dashboard-password"),
        json={"nonce": approval["nonce"]},
    )

    assert response.status_code == 403
    assert "missing ops action header" in response.text


def test_ops_approval_ui_can_approve_with_nonce(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")

    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("ops_approve_task"))
    approval = create_response.json()["approval"]

    response = client.post(
        f"/ops/approvals/{approval['id']}/approve",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "approval-decision"},
        json={"nonce": approval["nonce"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    with Session(get_engine()) as session:
        task = session.get(TaskModel, "ops_approve_task")
        audit = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.resource_id == approval["id"])
            .order_by(AuditEventModel.created_at.desc())
            .first()
        )
        assert task is not None
        assert task.enabled is True
        assert task.status == "enabled"
        assert audit is not None
        assert audit.actor_role == "ops_dashboard"
        assert audit.action == "approval.approve"


def test_ops_approval_ui_rejects_without_admin_key(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")

    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("ops_reject_task"))
    approval = create_response.json()["approval"]

    response = client.post(
        f"/ops/approvals/{approval['id']}/reject",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "approval-decision"},
        json={"reason": "not needed"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    with Session(get_engine()) as session:
        task = session.get(TaskModel, "ops_reject_task")
        assert task is not None
        assert task.enabled is False
        assert task.status == "rejected"


def test_ops_approval_invalid_nonce_fails(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")

    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("ops_bad_nonce"))
    approval = create_response.json()["approval"]

    response = client.post(
        f"/ops/approvals/{approval['id']}/approve",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "approval-decision"},
        json={"nonce": "wrong-nonce"},
    )

    assert response.status_code == 403
    with Session(get_engine()) as session:
        task = session.get(TaskModel, "ops_bad_nonce")
        assert task is not None
        assert task.enabled is False


def test_ops_routes_are_not_in_openapi(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/ops" not in paths
    assert "/ops/status" not in paths
    assert "/ops/audit" not in paths
    assert "/ops/runs/{run_id}" not in paths
    assert "/ops/tasks/{task_id}/run" not in paths
    assert "/ops/tasks/{task_id}/pause" not in paths
    assert "/ops/tasks/{task_id}/resume" not in paths
    assert "/ops/approvals/{approval_id}/approve" not in paths
    assert "/ops/approvals/{approval_id}/reject" not in paths
