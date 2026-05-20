from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import ApprovalModel, AuditEventModel, RunModel, TaskConfigVersionModel, TaskModel, utcnow
from app.services.task_version_service import record_task_config_version
from conftest import ADMIN_HEADERS, TOOL_HEADERS, sample_task


def task_change_payload(task_id: str, *, cron: str = "30 7 * * 1-5") -> dict:
    proposed = sample_task(task_id)
    proposed["trigger"]["cron"] = cron
    proposed["filters"]["include"] = ["Open WebUI", "Ollama", "security"]
    return {
        "requested_by": "yggdrasil",
        "summary": "Move the weekday digest earlier and add security filter.",
        "proposed_config": proposed,
    }


def capability_proposal_payload(**overrides) -> dict:
    payload = {
        "title": "Printer Supply Monitoring",
        "requested_by": "bragi",
        "source_channel": "discord",
        "original_request_preview": "Check my printer toner and warn me before it runs out.",
        "purpose": "Monitor approved printer supply status and notify before toner or ink levels become low.",
        "suggested_capability_id": "printer_supply_snmp.v1",
        "suggested_task_type": "printer_supply_snmp",
        "likely_approval_level": "L1_NOTIFY_ONLY",
        "required_inputs": ["approved printer ID", "polling schedule", "low-supply threshold"],
        "safety_rules": ["must not scan the LAN", "must not change printer configuration"],
        "non_goals": ["no arbitrary shell execution", "no printer administration changes"],
        "review_notes": "Useful but unsupported.",
    }
    payload.update(overrides)
    return payload


def source_proposal_payload(**overrides) -> dict:
    source = {
        "id": "ops_public_security_feed",
        "name": "Ops Public Security Feed",
        "type": "rss",
        "url": "https://example.com/security-feed.xml",
        "categories": ["operator_proposed", "security_news"],
        "trust_level": "operator_review",
        "enabled": True,
        "max_items": 5,
        "description": "Operator-reviewed source proposal from Bragi.",
        "region": "Global",
        "languages": ["en"],
        "source_type_label": "News feed",
        "update_cadence": "Regular",
        "ingestion_notes": "Use feed metadata/snippets unless terms allow more.",
        "ai_safe_fit": "B - terms-check/variable",
        "ingestion_mode": "feed_metadata",
    }
    source.update(overrides)
    return {"source": source, "summary": "Please review this public source.", "requested_by": "bragi"}


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
    assert "data-view=\"proposals\"" in allowed.text
    assert "data-view=\"capabilities\"" in allowed.text
    assert "data-view=\"sources\"" in allowed.text
    assert "data-count=\"proposals\"" in allowed.text
    assert "data-count=\"capabilities\"" in allowed.text
    assert "data-count=\"sources\"" in allowed.text
    assert "proposal-filter-q" in allowed.text
    assert "capability-filter-q" in allowed.text
    assert "capability-page-size" in allowed.text
    assert "data-capability-action" in allowed.text
    assert "source-filter-q" in allowed.text
    assert "source-filter-source-id" in allowed.text
    assert "source-page-size" in allowed.text
    assert "data-source-action" in allowed.text
    assert "data-source-detail-id" in allowed.text
    assert "pending_sources" in allowed.text
    assert "Plan implementation" in allowed.text
    assert "implementation_planned" in allowed.text
    assert "approval-filter-q" in allowed.text
    assert "task-detail" in allowed.text
    assert "data-task-detail-id" in allowed.text
    assert "Status Control" in allowed.text
    assert "data-task-status-select" in allowed.text
    assert "data-task-status-apply" in allowed.text
    assert "data-task-archive" in allowed.text
    assert "data-task-version-revert" in allowed.text
    assert "task-filter-text" in allowed.text
    assert "task-page-size" in allowed.text
    assert "run-filter-status" in allowed.text
    assert "run-filter-task-id" in allowed.text
    assert "run-filter-notification-sent" in allowed.text
    assert "run-page-size" in allowed.text
    assert "run-timeline" in allowed.text
    assert "data-task-timeline-id" in allowed.text
    assert "proposal-page-size" in allowed.text
    assert "approval-page-size" in allowed.text
    assert "audit-filter-action" in allowed.text
    assert "audit-page-size" in allowed.text
    assert "source.propose" in allowed.text
    assert "source_proposal" in allowed.text
    assert "task-pagination" in allowed.text
    assert "run-pagination" in allowed.text
    assert "data-sort-view" in allowed.text
    assert "sortHeader('tasks', 'Task', 'id')" in allowed.text
    assert "sortHeader('runs', 'Created', 'created_at')" in allowed.text
    assert "sortHeader('audit', 'Action', 'action')" in allowed.text
    assert "saved-view-select" in allowed.text
    assert "failed_runs" in allowed.text
    assert "pending_capabilities" in allowed.text
    assert "recent_discord_sends" in allowed.text
    assert "worker_activity" in allowed.text
    assert "runTimelineContext" in allowed.text
    assert "run-summary-strip" in allowed.text
    assert "sent_discord_count" in allowed.text


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
    assert body["counts"]["pending_proposals"] == 0
    assert body["counts"]["pending_general_approvals"] == 1
    assert body["counts"]["pending_capability_proposals"] == 0
    assert body["tasks"][0]["latest_run"]["id"] == run_id
    assert body["recent_runs"][0]["notification"]["sent"] is True
    assert body["pending_approvals"][0]["review"]["actions"]
    assert body["pending_general_approvals"][0]["id"] == "approval-ops-test"
    assert body["pending_proposals"] == []
    assert body["pending_capability_proposals"] == []
    assert body["pending_approvals"][0]["review"]["failure_mode"]
    assert body["pending_approvals"][0]["review"]["config_change"]["enabled_after_approval"] is True
    assert "nonce" not in response.text.lower()
    assert "super-secret-value" not in response.text


def test_ops_status_and_queue_show_task_change_proposals(client):
    task_id = "ops_task_change_visible"
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task(task_id))
    assert create_response.status_code == 201
    proposal_response = client.post(
        f"/tasks/{task_id}/propose-change",
        headers=TOOL_HEADERS,
        json=task_change_payload(task_id),
    )
    assert proposal_response.status_code == 201
    proposal = proposal_response.json()

    status_response = client.get("/ops/status", headers=ADMIN_HEADERS)
    list_response = client.get(f"/ops/task-change-proposals?task_id={task_id}", headers=ADMIN_HEADERS)
    detail_response = client.get(f"/ops/task-change-proposals/{proposal['id']}", headers=ADMIN_HEADERS)

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["counts"]["pending_task_change_proposals"] == 1
    assert status_body["counts"]["open_task_change_proposals"] == 1
    assert status_body["pending_task_change_proposals"][0]["id"] == proposal["id"]
    assert "nonce" not in status_response.text.lower()
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["proposals"]] == [proposal["id"]]
    assert detail_response.status_code == 200
    assert detail_response.json()["proposed_config"]["trigger"]["cron"] == "30 7 * * 1-5"
    assert "nonce" not in detail_response.text.lower()


def test_ops_status_and_queue_show_capability_proposals(client):
    first = client.post("/capability-proposals/draft", headers=TOOL_HEADERS, json=capability_proposal_payload())
    second = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(
            title="UPS Battery Monitoring",
            original_request_preview="Warn me before the UPS battery fails.",
            purpose="Monitor an approved UPS battery status source and notify before the battery becomes unhealthy.",
            suggested_capability_id="ups_battery_status.v1",
            suggested_task_type="ups_battery_status",
            source_channel="openwebui",
        ),
    )
    assert first.status_code == 201
    assert second.status_code == 201

    status_response = client.get("/ops/status", headers=ADMIN_HEADERS)
    list_response = client.get(
        "/ops/capability-proposals?status=pending&page=1&page_size=5&q=printer",
        headers=ADMIN_HEADERS,
    )
    detail_response = client.get(f"/ops/capability-proposals/{first.json()['id']}", headers=ADMIN_HEADERS)
    too_small = client.get("/ops/capability-proposals?page_size=4", headers=ADMIN_HEADERS)

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["counts"]["pending_capability_proposals"] == 2
    assert status_body["counts"]["pending_reviews"] == 2
    assert status_body["pending_capability_proposals"][0]["execution"] == {
        "creates_task": False,
        "creates_approval": False,
        "can_be_applied": False,
    }
    assert "nonce" not in status_response.text.lower()
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["pagination"]["min_page_size"] == 5
    assert list_body["counts"]["matched"] == 1
    assert list_body["proposals"][0]["suggested_capability_id"] == "printer_supply_snmp.v1"
    assert detail_response.status_code == 200
    assert detail_response.json()["original_request_preview"] == "Check my printer toner and warn me before it runs out."
    assert too_small.status_code == 422


def test_ops_can_accept_reject_and_close_capability_proposals_with_action_header(client):
    accepted = client.post("/capability-proposals/draft", headers=TOOL_HEADERS, json=capability_proposal_payload()).json()
    rejected = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(
            title="UPS Battery Monitoring",
            purpose="Monitor an approved UPS battery status source and notify before the battery becomes unhealthy.",
            suggested_capability_id="ups_battery_status.v1",
            suggested_task_type="ups_battery_status",
        ),
    ).json()
    closed = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(
            title="NAS Disk Health Monitoring",
            purpose="Monitor an approved NAS disk health status source and notify before the disk becomes unhealthy.",
            suggested_capability_id="nas_disk_health.v1",
            suggested_task_type="nas_disk_health",
        ),
    ).json()

    missing_header = client.post(
        f"/ops/capability-proposals/{accepted['id']}/accept",
        headers=ADMIN_HEADERS,
        json={"reason": "Useful."},
    )
    accept_response = client.post(
        f"/ops/capability-proposals/{accepted['id']}/accept",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Implement later."},
    )
    reject_response = client.post(
        f"/ops/capability-proposals/{rejected['id']}/reject",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Not needed."},
    )
    close_response = client.post(
        f"/ops/capability-proposals/{closed['id']}/close",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Duplicate."},
    )
    repeat_accept = client.post(
        f"/ops/capability-proposals/{accepted['id']}/accept",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Again."},
    )

    assert missing_header.status_code == 403
    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "accepted"
    assert "Implement later" in accept_response.json()["review_notes"]
    assert accept_response.json()["execution"]["creates_task"] is False
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    assert "Not needed" in reject_response.json()["review_notes"]
    assert close_response.status_code == 200
    assert close_response.json()["status"] == "closed"
    assert "Duplicate" in close_response.json()["review_notes"]
    assert repeat_accept.status_code == 409
    with Session(get_engine()) as session:
        audits = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.resource_type == "capability_proposal")
            .filter(
                AuditEventModel.action.in_(
                    ["capability.accepted", "capability.rejected", "capability.closed"]
                )
            )
            .order_by(AuditEventModel.created_at)
            .all()
        )
        assert [audit.action for audit in audits] == [
            "capability.accepted",
            "capability.rejected",
            "capability.closed",
        ]


def test_ops_can_approve_and_apply_tool_created_source_proposal_without_nonce(client):
    created = client.post("/sources/propose", headers=TOOL_HEADERS, json=source_proposal_payload()).json()
    assert "nonce" not in created

    status_response = client.get("/ops/status", headers=ADMIN_HEADERS)
    list_response = client.get("/ops/source-proposals?status=pending&page_size=5", headers=ADMIN_HEADERS)
    detail_response = client.get(f"/ops/source-proposals/{created['id']}", headers=ADMIN_HEADERS)
    missing_header = client.post(
        f"/ops/source-proposals/{created['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"reason": "Looks safe."},
    )
    approve_response = client.post(
        f"/ops/source-proposals/{created['id']}/approve",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "source-proposal"},
        json={"reason": "Looks safe."},
    )
    apply_response = client.post(
        f"/ops/source-proposals/{created['id']}/apply",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "source-proposal"},
        json={"reason": "Generate reviewed YAML."},
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["counts"]["pending_source_proposals"] == 1
    assert status_body["counts"]["pending_reviews"] == 1
    assert status_body["pending_source_proposals"][0]["source_id"] == "ops_public_security_feed"
    assert "nonce" not in status_response.text.lower()
    assert list_response.status_code == 200
    assert list_response.json()["proposals"][0]["source_id"] == "ops_public_security_feed"
    assert list_response.json()["pagination"]["min_page_size"] == 5
    assert detail_response.status_code == 200
    assert detail_response.json()["source"]["url"] == "https://example.com/security-feed.xml"
    assert missing_header.status_code == 403
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"
    assert approve_response.json()["execution"]["can_be_applied"] is True
    assert apply_response.status_code == 200
    applied = apply_response.json()
    assert applied["proposal"]["status"] == "applied"
    assert applied["apply"]["source_entry"]["id"] == "ops_public_security_feed"
    assert "configs/sources/approved_sources.yaml" in applied["apply"]["registry_file"]
    assert "nonce" not in apply_response.text.lower()

    with Session(get_engine()) as session:
        audits = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.resource_type == "source_proposal")
            .filter(AuditEventModel.action.in_(["source.approve", "source.apply"]))
            .order_by(AuditEventModel.created_at)
            .all()
        )
        assert [audit.action for audit in audits] == ["source.approve", "source.apply"]


def test_ops_can_plan_and_supersede_accepted_capability_proposal(client):
    proposal = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(),
    ).json()

    plan_before_accept = client.post(
        f"/ops/capability-proposals/{proposal['id']}/plan",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Plan too early."},
    )
    accept_response = client.post(
        f"/ops/capability-proposals/{proposal['id']}/accept",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Useful pilot."},
    )
    plan_response = client.post(
        f"/ops/capability-proposals/{proposal['id']}/plan",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Plan it."},
    )
    repeat_plan = client.post(
        f"/ops/capability-proposals/{proposal['id']}/plan",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Plan again."},
    )
    mark_implemented = client.post(
        f"/ops/capability-proposals/{proposal['id']}/implemented",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Done."},
    )
    list_response = client.get(
        "/capability-proposals?status=implementation_planned",
        headers=TOOL_HEADERS,
    )
    ops_list_response = client.get(
        "/ops/capability-proposals?status=implementation_planned&page_size=5",
        headers=ADMIN_HEADERS,
    )
    supersede_response = client.post(
        f"/ops/capability-proposals/{proposal['id']}/supersede",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Better capability will replace this."},
    )

    assert plan_before_accept.status_code == 409
    assert "must be accepted" in plan_before_accept.text
    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "accepted"
    assert plan_response.status_code == 200
    planned = plan_response.json()
    assert planned["status"] == "implementation_planned"
    assert planned["implementation_plan"]["status"] == "implementation_planned"
    assert planned["implementation_plan"]["execution"] == {
        "creates_task": False,
        "creates_approval": False,
        "can_be_applied": False,
    }
    assert "configs/capabilities.yaml" in planned["implementation_plan"]["files_to_change"]
    assert "automation-worker/worker/handlers/printer_supply_snmp.py" in planned["implementation_plan"]["files_to_change"]
    assert repeat_plan.status_code == 409
    assert mark_implemented.status_code == 409
    assert "not registered yet" in mark_implemented.text
    assert list_response.status_code == 200
    assert list_response.json()[0]["implementation_plan"]["capability_id"] == "printer_supply_snmp.v1"
    assert ops_list_response.status_code == 200
    assert ops_list_response.json()["proposals"][0]["implementation_plan"]["required_decisions"]
    assert supersede_response.status_code == 200
    assert supersede_response.json()["status"] == "superseded"
    assert supersede_response.json()["implementation_plan"]["status"] == "superseded"
    assert "Better capability" in supersede_response.json()["implementation_plan"]["review_notes"]
    with Session(get_engine()) as session:
        audits = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.resource_type == "capability_proposal")
            .filter(
                AuditEventModel.action.in_(
                    ["capability.accepted", "capability.implementation_planned", "capability.superseded"]
                )
            )
            .order_by(AuditEventModel.created_at)
            .all()
        )
        assert [audit.action for audit in audits] == [
            "capability.accepted",
            "capability.implementation_planned",
            "capability.superseded",
        ]


def test_ops_can_approve_and_apply_task_change_proposal_with_action_header(client):
    task_id = "ops_task_change_apply"
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task(task_id))
    assert create_response.status_code == 201
    proposal = client.post(
        f"/tasks/{task_id}/propose-change",
        headers=TOOL_HEADERS,
        json=task_change_payload(task_id, cron="45 6 * * 1-5"),
    ).json()

    missing_header = client.post(
        f"/ops/task-change-proposals/{proposal['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"nonce": proposal["nonce"]},
    )
    bad_nonce = client.post(
        f"/ops/task-change-proposals/{proposal['id']}/approve",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "task-change-proposal"},
        json={"nonce": "wrong-nonce"},
    )
    approve_response = client.post(
        f"/ops/task-change-proposals/{proposal['id']}/approve",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "task-change-proposal"},
        json={"nonce": proposal["nonce"]},
    )
    apply_response = client.post(
        f"/ops/task-change-proposals/{proposal['id']}/apply",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "task-change-proposal"},
    )

    assert missing_header.status_code == 403
    assert bad_nonce.status_code == 403
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"
    assert apply_response.status_code == 200
    assert apply_response.json()["proposal"]["status"] == "applied"
    assert apply_response.json()["task"]["trigger"]["cron"] == "45 6 * * 1-5"
    with Session(get_engine()) as session:
        audits = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.resource_type == "task_change_proposal")
            .filter(AuditEventModel.resource_id == proposal["id"])
            .order_by(AuditEventModel.created_at)
            .all()
        )
        assert [audit.action for audit in audits][-2:] == ["task_change.approve", "task_change.apply"]


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
                        "approved_source_count": 3,
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
                        "source_health": [
                            {
                                "source": "open_webui_releases",
                                "source_id": "open_webui_releases",
                                "url": "https://example.com/secret-ish-but-not-needed",
                                "status": "ok",
                                "item_count": 1,
                                "trust_level": "official_project_release_feed",
                                "ingestion_mode": "feed_metadata",
                            }
                        ],
                        "quality": {
                            "enabled": True,
                            "status": "degraded",
                            "alert_needed": True,
                            "alert_target": "alerts",
                            "metrics": {
                                "item_count": 1,
                                "successful_source_count": 1,
                                "processed_source_count": 2,
                                "configured_source_count": 4,
                            },
                            "thresholds": {"min_items": 5, "alert_on_delivery_failure": True},
                            "reasons": [{"code": "source_errors", "message": "Digest recorded 1 source error."}],
                        },
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
                    "quality_alert": {
                        "decision": {"send": True, "classification": "failure"},
                        "notification": {"sent": True, "target": "alerts"},
                    },
                    "observability": {
                        "item_count": 1,
                        "deduplicated_count": 1,
                        "successful_source_count": 1,
                        "processed_source_count": 2,
                        "message_char_count": 11,
                        "quality_status": "degraded",
                        "delivery": {"sent": True, "target": "briefings", "decision_reason": "enabled"},
                        "n8n": {"enabled": True, "status": "ready", "status_code": 200},
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
    assert body["observability"]["item_count"] == 1
    assert body["observability"]["delivery"]["target"] == "briefings"
    assert body["observability"]["n8n"]["status_code"] == 200
    assert body["digest"]["item_count"] == 1
    assert body["digest"]["error_count"] == 1
    assert body["digest"]["summary_mode"] == "llm"
    assert body["digest"]["approved_source_count"] == 3
    assert body["digest"]["quality"]["status"] == "degraded"
    assert body["digest"]["quality"]["reasons"][0]["code"] == "source_errors"
    assert body["digest"]["source_health"][0]["source_id"] == "open_webui_releases"
    assert "secret-ish-but-not-needed" not in response.text
    assert body["digest"]["items"][0]["url"] == "https://example.com/open-webui"
    assert body["n8n"]["webhook_id"] == "daily_briefing_stub"
    assert body["n8n"]["response"]["action"] == "normalize_digest_payload"
    assert body["n8n"]["response"]["authorization"] == "[REDACTED]"
    assert body["notification_decision"]["send"] is True
    assert body["notification_decision"]["secret_token"] == "[REDACTED]"
    assert body["notification"]["sent"] is True
    assert body["notification"]["discord_token"] == "[REDACTED]"
    assert body["quality_alert"]["notification"]["target"] == "alerts"
    assert "hidden-secret" not in response.text
    assert "api_token" not in response.text


def test_ops_task_detail_redacts_config_and_lists_history_runs_and_actions(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task_id = "ops_task_detail"
    run_id = str(uuid.uuid4())
    config = sample_task(task_id, enabled=True, runtime={"dry_run": False})
    config["api_token"] = "hidden-secret"
    config["nested"] = {"authorization": "Bearer hidden-secret"}
    with Session(get_engine()) as session:
        task_model = TaskModel(
            id=task_id,
            name="Ops Task Detail",
            type=config["type"],
            enabled=True,
            owner=config["owner"],
            created_by=config["created_by"],
            approval_level=config["policy"]["approval_level"],
            status="enabled",
            config=config,
        )
        session.add(task_model)
        session.flush()
        record_task_config_version(
            session,
            task_model,
            actor_role="tool",
            change_type="draft",
            approval_id="approval-task-detail",
            summary="Task detail test snapshot.",
        )
        session.add_all(
            [
                ApprovalModel(
                    id="approval-task-detail",
                    task_id=task_id,
                    approval_level=config["policy"]["approval_level"],
                    requested_by="yggdrasil",
                    status="approved",
                    summary="Approved task detail test",
                    risk=config["policy"]["approval_level"],
                    nonce_hash="nonce-hash-secret",
                    created_at=utcnow(),
                    decided_at=utcnow(),
                ),
                RunModel(
                    id=run_id,
                    task_id=task_id,
                    status="completed",
                    log={"result": {"status": "ready"}, "api_token": "hidden-secret"},
                    created_at=utcnow(),
                    completed_at=utcnow(),
                ),
            ]
        )
        session.commit()

    response = client.get(f"/ops/tasks/{task_id}", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["id"] == task_id
    assert body["config"]["api_token"] == "[REDACTED]"
    assert body["config"]["nested"]["authorization"] == "[REDACTED]"
    assert body["approvals"][0]["id"] == "approval-task-detail"
    assert body["approvals"][0]["status"] == "approved"
    assert "nonce_hash" not in body["approvals"][0]
    assert body["recent_runs"][0]["id"] == run_id
    assert body["config_versions"][0]["version"] == 1
    assert body["config_versions"][0]["approval_id"] == "approval-task-detail"
    assert body["config_versions"][0]["diff"]["counts"]["added"] > 0
    assert body["allowed_actions"]["dry_run"]["allowed"] is True
    assert body["allowed_actions"]["live_run"]["allowed"] is True
    assert body["allowed_actions"]["pause"]["allowed"] is True
    assert body["allowed_actions"]["resume"]["allowed"] is False
    assert "hidden-secret" not in response.text
    assert "nonce-hash-secret" not in response.text


def test_ops_task_detail_reports_l2_actions_blocked(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    config = sample_task("ops_task_detail_l2", "L2_LOCAL_WRITE", enabled=True)
    with Session(get_engine()) as session:
        session.add(
            TaskModel(
                id=config["id"],
                name=config["name"],
                type=config["type"],
                enabled=True,
                owner=config["owner"],
                created_by=config["created_by"],
                approval_level=config["policy"]["approval_level"],
                status="enabled",
                config=config,
            )
        )
        session.commit()

    response = client.get("/ops/tasks/ops_task_detail_l2", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 200
    actions = response.json()["allowed_actions"]
    assert actions["dry_run"]["allowed"] is True
    assert actions["live_run"]["allowed"] is False
    assert "L2+" in actions["live_run"]["reason"]
    assert actions["pause"]["allowed"] is False
    assert "pause L2+" in actions["pause"]["reason"]
    assert actions["resume"]["allowed"] is False
    assert "resume L2+" in actions["resume"]["reason"]


def test_ops_audit_events_are_redacted_and_paginated(client, monkeypatch):
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

    response = client.get("/ops/audit?page=1&page_size=5", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 200
    body = response.json()
    assert body["page_size"] == 5
    assert body["pagination"]["min_page_size"] == 5
    assert body["pagination"]["total"] == 2
    assert len(body["events"]) == 2
    event = body["events"][0]
    assert event["action"] == "task.run"
    assert event["resource_id"] == "daily_local_ai_security_briefing"
    assert event["detail"]["api_token"] == "[REDACTED]"
    assert event["detail"]["nested"]["authorization"] == "[REDACTED]"
    assert "hidden-secret" not in response.text


def test_ops_audit_rejects_page_size_below_minimum(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")

    response = client.get("/ops/audit?page_size=4", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 422


def test_ops_audit_events_can_be_sorted(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    with Session(get_engine()) as session:
        session.add_all(
            [
                AuditEventModel(
                    actor_role="worker",
                    action="run.update",
                    resource_type="run",
                    resource_id="run-sort-test",
                    detail={},
                    created_at=utcnow(),
                ),
                AuditEventModel(
                    actor_role="admin",
                    action="approval.approve",
                    resource_type="approval",
                    resource_id="approval-sort-test",
                    detail={},
                    created_at=utcnow(),
                ),
            ]
        )
        session.commit()

    response = client.get(
        "/ops/audit?page=1&page_size=5&sort_by=action&sort_dir=asc",
        auth=("operator", "test-dashboard-password"),
    )
    invalid = client.get("/ops/audit?sort_by=detail", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 200
    body = response.json()
    assert body["sort"] == {"by": "action", "dir": "asc"}
    assert [event["action"] for event in body["events"]] == ["approval.approve", "run.update"]
    assert invalid.status_code == 422


def test_ops_runs_are_filtered_and_paginated(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    now = utcnow()
    with Session(get_engine()) as session:
        session.add_all(
            [
                RunModel(
                    id=f"run-pagination-{index}",
                    task_id="daily_local_ai_security_briefing" if index < 6 else "other_task",
                    status="completed_dry_run" if index < 6 else "failed",
                    log={"result": {"status": "ready"}, "notification": {"sent": False}},
                    created_at=now + timedelta(seconds=index),
                    completed_at=now + timedelta(seconds=index),
                )
                for index in range(7)
            ]
        )
        session.commit()

    first_page = client.get(
        "/ops/runs?task_id=daily_local_ai_security_briefing&status=dry_run&page=1&page_size=5",
        auth=("operator", "test-dashboard-password"),
    )
    second_page = client.get(
        "/ops/runs?task_id=daily_local_ai_security_briefing&status=dry_run&page=2&page_size=5",
        auth=("operator", "test-dashboard-password"),
    )
    too_small = client.get("/ops/runs?page_size=4", auth=("operator", "test-dashboard-password"))
    sorted_asc = client.get(
        "/ops/runs?page=1&page_size=5&sort_by=created_at&sort_dir=asc",
        auth=("operator", "test-dashboard-password"),
    )
    invalid_sort = client.get("/ops/runs?sort_by=log", auth=("operator", "test-dashboard-password"))

    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["pagination"]["total"] == 6
    assert first_body["pagination"]["returned"] == 5
    assert first_body["pagination"]["has_next"] is True
    assert first_body["summary"]["total"] == 6
    assert first_body["summary"]["success_count"] == 6
    assert first_body["summary"]["failure_count"] == 0
    assert first_body["summary"]["dry_run_count"] == 6
    assert first_body["summary"]["sent_discord_count"] == 0
    assert first_body["summary"]["last_failure_at"] is None
    assert all(run["task_id"] == "daily_local_ai_security_briefing" for run in first_body["runs"])
    assert second_page.status_code == 200
    assert second_page.json()["pagination"]["returned"] == 1
    assert too_small.status_code == 422
    assert sorted_asc.status_code == 200
    assert sorted_asc.json()["sort"] == {"by": "created_at", "dir": "asc"}
    assert [run["id"] for run in sorted_asc.json()["runs"][:3]] == [
        "run-pagination-0",
        "run-pagination-1",
        "run-pagination-2",
    ]
    assert invalid_sort.status_code == 422


def test_ops_runs_can_filter_notification_sent(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    now = utcnow()
    with Session(get_engine()) as session:
        session.add_all(
            [
                RunModel(
                    id="run-notification-sent",
                    task_id="daily_local_ai_security_briefing",
                    status="completed",
                    log={"result": {"status": "ready"}, "notification": {"sent": True, "target": "briefings"}},
                    created_at=now,
                    completed_at=now,
                ),
                RunModel(
                    id="run-notification-unsent",
                    task_id="daily_local_ai_security_briefing",
                    status="completed_dry_run",
                    log={"result": {"status": "ready"}, "notification": {"sent": False, "target": "briefings"}},
                    created_at=now + timedelta(seconds=1),
                    completed_at=now + timedelta(seconds=1),
                ),
                RunModel(
                    id="run-notification-missing",
                    task_id="daily_local_ai_security_briefing",
                    status="completed",
                    log={"result": {"status": "ready"}},
                    created_at=now + timedelta(seconds=2),
                    completed_at=now + timedelta(seconds=2),
                ),
            ]
        )
        session.commit()

    sent = client.get(
        "/ops/runs?notification_sent=true&page=1&page_size=5",
        auth=("operator", "test-dashboard-password"),
    )
    unsent = client.get(
        "/ops/runs?notification_sent=false&page=1&page_size=5",
        auth=("operator", "test-dashboard-password"),
    )
    invalid = client.get("/ops/runs?notification_sent=maybe", auth=("operator", "test-dashboard-password"))

    assert sent.status_code == 200
    assert sent.json()["filters"]["notification_sent"] == "true"
    assert [run["id"] for run in sent.json()["runs"]] == ["run-notification-sent"]
    assert sent.json()["summary"]["sent_discord_count"] == 1
    assert sent.json()["summary"]["total"] == 1
    assert unsent.status_code == 200
    assert [run["id"] for run in unsent.json()["runs"]] == ["run-notification-unsent"]
    assert unsent.json()["summary"]["sent_discord_count"] == 0
    assert unsent.json()["summary"]["total"] == 1
    assert invalid.status_code == 422


def test_ops_audit_events_can_be_filtered(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    with Session(get_engine()) as session:
        session.add_all(
            [
                AuditEventModel(
                    actor_role="tool",
                    action="task.draft",
                    resource_type="task",
                    resource_id="daily_local_ai_security_briefing",
                    detail={"approval_level": "L1_NOTIFY_ONLY"},
                    created_at=utcnow(),
                ),
                AuditEventModel(
                    actor_role="ops_dashboard",
                    action="task.pause",
                    resource_type="task",
                    resource_id="daily_local_ai_security_briefing",
                    detail={"surface": "ops_ui"},
                    created_at=utcnow(),
                ),
                AuditEventModel(
                    actor_role="worker",
                    action="run.update",
                    resource_type="run",
                    resource_id="other-run",
                    detail={"status": "completed"},
                    created_at=utcnow(),
                ),
            ]
        )
        session.commit()

    response = client.get(
        "/ops/audit?actor_role=ops_dashboard&action=task.pause&resource_type=task&q=daily",
        auth=("operator", "test-dashboard-password"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filters"]["actor_role"] == "ops_dashboard"
    assert body["filters"]["action"] == "task.pause"
    assert body["filters"]["resource_type"] == "task"
    assert body["filters"]["q"] == "daily"
    assert [event["action"] for event in body["events"]] == ["task.pause"]
    assert body["events"][0]["resource_id"] == "daily_local_ai_security_briefing"


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


def test_ops_task_archive_requires_action_header(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("ops_archive_header"))
    assert create_response.status_code == 201

    response = client.post("/ops/tasks/ops_archive_header/archive", auth=("operator", "test-dashboard-password"))

    assert response.status_code == 403
    assert "missing ops task archive action header" in response.text


def test_ops_task_archive_hides_disabled_task_and_rejects_pending_approval(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task_id = "ops_archive_pending_task"
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task(task_id))
    assert create_response.status_code == 201
    approval_id = create_response.json()["approval"]["id"]

    response = client.post(
        f"/ops/tasks/{task_id}/archive",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-archive"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["status"] == "archived"
    assert "audit history and run history retained" in body["message"]

    status_response = client.get("/ops/status", auth=("operator", "test-dashboard-password"))
    assert status_response.status_code == 200
    assert task_id not in {task["id"] for task in status_response.json()["tasks"]}

    with Session(get_engine()) as session:
        approval = session.get(ApprovalModel, approval_id)
        task = session.get(TaskModel, task_id)
        version = (
            session.query(TaskConfigVersionModel)
            .filter(TaskConfigVersionModel.task_id == task_id)
            .order_by(TaskConfigVersionModel.version.desc())
            .first()
        )
        audit = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.action == "task.archive")
            .filter(AuditEventModel.resource_id == task_id)
            .first()
        )
        assert approval is not None
        assert approval.status == "rejected"
        assert task is not None
        assert task.enabled is False
        assert task.status == "archived"
        assert task.config["enabled"] is False
        assert version is not None
        assert version.change_type == "archive"
        assert version.actor_role == "ops_dashboard"
        assert audit is not None
        assert audit.actor_role == "ops_dashboard"
        assert audit.detail["surface"] == "ops_ui"
        assert audit.detail["rejected_pending_approvals"] == [approval_id]


def test_ops_task_archive_rejects_enabled_task(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task = sample_task("ops_archive_enabled", enabled=True)
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
        "/ops/tasks/ops_archive_enabled/archive",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "task-archive"},
    )

    assert response.status_code == 409
    assert "paused before delete/archive" in response.text


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


def test_ops_task_version_revert_requires_action_header(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    create_response = client.post(
        "/tasks/draft",
        headers=TOOL_HEADERS,
        json=sample_task("ops_revert_header", "L0_READ_ONLY"),
    )
    assert create_response.status_code == 201

    response = client.post(
        "/ops/tasks/ops_revert_header/versions/1/revert",
        auth=("operator", "test-dashboard-password"),
        json={"reason": "missing header"},
    )

    assert response.status_code == 403
    assert "missing ops version revert action header" in response.text


def test_ops_task_version_revert_creates_disabled_approval_gated_draft(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    task_id = "ops_revert_version"
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task(task_id, "L0_READ_ONLY"))
    assert create_response.status_code == 201
    updated = sample_task(
        task_id,
        "L0_READ_ONLY",
        enabled=True,
        trigger={"cron": "15 8 * * 1-5"},
        filters={"include": ["Open WebUI", "Ollama"]},
    )
    update_response = client.put(f"/tasks/{task_id}", headers=ADMIN_HEADERS, json=updated)
    assert update_response.status_code == 200
    assert update_response.json()["enabled"] is True

    response = client.post(
        f"/ops/tasks/{task_id}/versions/1/revert",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "version-revert"},
        json={"reason": "test revert"},
    )

    assert response.status_code == 200
    body = response.json()
    approval = body["approval"]
    assert body["message"].startswith("revert draft created")
    assert body["task"]["enabled"] is False
    assert body["task"]["status"] == "pending_approval"
    assert body["source_version"]["version"] == 1
    assert body["new_version"]["version"] == 3
    assert body["new_version"]["change_type"] == "revert_draft"
    assert body["new_version"]["approval_id"] == approval["id"]
    assert body["new_version"]["diff"]["counts"]["changed"] > 0
    assert body["approval_nonce"]

    with Session(get_engine()) as session:
        task = session.get(TaskModel, task_id)
        versions = (
            session.query(TaskConfigVersionModel)
            .filter(TaskConfigVersionModel.task_id == task_id)
            .order_by(TaskConfigVersionModel.version)
            .all()
        )
        audit = (
            session.query(AuditEventModel)
            .filter(AuditEventModel.action == "task.config.revert")
            .filter(AuditEventModel.resource_id == task_id)
            .first()
        )
        assert task is not None
        assert task.enabled is False
        assert task.status == "pending_approval"
        assert task.config["enabled"] is False
        assert task.config["trigger"]["cron"] == "0 8 * * 1-5"
        assert [version.version for version in versions] == [1, 2, 3]
        assert versions[-1].approval_id == approval["id"]
        assert audit is not None
        assert audit.detail["source_version"] == 1
        assert audit.detail["new_version"] == 3

    status_response = client.get("/ops/status", auth=("operator", "test-dashboard-password"))
    assert status_response.status_code == 200
    proposal = next(item for item in status_response.json()["pending_proposals"] if item["id"] == approval["id"])
    assert proposal["review"]["config_diff"]["change_type"] == "revert_draft"

    approve_response = client.post(
        f"/ops/approvals/{approval['id']}/approve",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "approval-decision"},
        json={"nonce": body["approval_nonce"]},
    )

    assert approve_response.status_code == 200
    with Session(get_engine()) as session:
        task = session.get(TaskModel, task_id)
        latest = (
            session.query(TaskConfigVersionModel)
            .filter(TaskConfigVersionModel.task_id == task_id)
            .order_by(TaskConfigVersionModel.version.desc())
            .first()
        )
        assert task is not None
        assert task.enabled is True
        assert task.status == "enabled"
        assert task.config["enabled"] is True
        assert task.config["trigger"]["cron"] == "0 8 * * 1-5"
        assert latest is not None
        assert latest.version == 4
        assert latest.change_type == "approval_approve"


def test_ops_task_version_revert_rejects_current_version(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_USER", "operator")
    monkeypatch.setenv("AUTOMATION_OPS_DASHBOARD_PASSWORD", "test-dashboard-password")
    create_response = client.post(
        "/tasks/draft",
        headers=TOOL_HEADERS,
        json=sample_task("ops_revert_current", "L0_READ_ONLY"),
    )
    assert create_response.status_code == 201

    response = client.post(
        "/ops/tasks/ops_revert_current/versions/1/revert",
        auth=("operator", "test-dashboard-password"),
        headers={"X-Yggy-Ops-Action": "version-revert"},
        json={"reason": "current version"},
    )

    assert response.status_code == 409
    assert "current version" in response.text


def test_ops_routes_are_not_in_openapi(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/ops" not in paths
    assert "/ops/status" not in paths
    assert "/ops/audit" not in paths
    assert "/ops/reviews" not in paths
    assert "/ops/runs" not in paths
    assert "/ops/runs/{run_id}" not in paths
    assert "/ops/task-change-proposals" not in paths
    assert "/ops/task-change-proposals/{proposal_id}" not in paths
    assert "/ops/task-change-proposals/{proposal_id}/approve" not in paths
    assert "/ops/task-change-proposals/{proposal_id}/reject" not in paths
    assert "/ops/task-change-proposals/{proposal_id}/apply" not in paths
    assert "/ops/tasks/{task_id}" not in paths
    assert "/ops/tasks/{task_id}/run" not in paths
    assert "/ops/tasks/{task_id}/pause" not in paths
    assert "/ops/tasks/{task_id}/resume" not in paths
    assert "/ops/tasks/{task_id}/archive" not in paths
    assert "/ops/tasks/{task_id}/versions/{version}/revert" not in paths
    assert "/ops/approvals/{approval_id}/approve" not in paths
    assert "/ops/approvals/{approval_id}/reject" not in paths
