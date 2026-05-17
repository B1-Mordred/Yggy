from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import notify_pending_task_changes  # noqa: E402


def sample_proposal(**overrides):
    proposal = {
        "id": "proposal-123",
        "task_id": "daily_local_ai_security_briefing",
        "status": "pending",
        "approval_level": "L1_NOTIFY_ONLY",
        "requested_by": "yggdrasil",
        "created_at": "2026-05-17T13:00:00Z",
        "summary": "Move the weekday digest earlier.",
        "nonce": "must-not-render",
        "nonce_hash": "must-not-render",
        "admin_key": "must-not-render",
        "risk": {
            "severity": "operator_review",
            "categories": {"schedule": ["trigger.cron"], "sources": ["filters.include"]},
            "base_enabled": False,
            "proposed_enabled": False,
        },
        "diff": {
            "counts": {"added": 0, "removed": 0, "changed": 2},
            "changed": [
                {"path": "trigger.cron", "before": "0 8 * * 1-5", "after": "30 7 * * 1-5"},
                {"path": "filters.include", "before": ["Open WebUI"], "after": ["Open WebUI", "security"]},
            ],
            "added": [],
            "removed": [],
            "truncated": False,
        },
    }
    proposal.update(overrides)
    return proposal


def test_render_task_change_notification_excludes_nonce_and_admin_material():
    message = notify_pending_task_changes.render_task_change_notification(sample_proposal())

    assert "Task change proposal pending" in message
    assert "daily_local_ai_security_briefing" in message
    assert "Nonce: not included" in message
    assert "must-not-render" not in message
    assert "nonce_hash" not in message
    assert "admin_key" not in message
    assert "Changed `trigger.cron`" in message


def test_selected_task_change_proposals_filters_by_id():
    proposals = [sample_proposal(id="one"), sample_proposal(id="two")]

    selected = notify_pending_task_changes.selected_task_change_proposals(proposals, proposal_id="two")

    assert [item["id"] for item in selected] == ["two"]


def test_send_task_change_notifications_dry_run_posts_to_approvals_target():
    calls = []

    def fake_request(method, path, *, base_url, api_key, payload=None, timeout=15):
        calls.append({"method": method, "path": path, "payload": payload})
        if path.startswith("/task-change-proposals"):
            return [sample_proposal()]
        if path == "/notifications/discord/send":
            return {"sent": False, "dry_run": payload["dry_run"], "target": payload["target"]}
        raise AssertionError(path)

    result = notify_pending_task_changes.send_task_change_notifications(
        base_url="http://127.0.0.1:8088",
        api_key="test-admin-key",
        dry_run=True,
        request_fn=fake_request,
    )

    assert result["selected_count"] == 1
    assert result["sent_count"] == 1
    assert calls[1]["path"] == "/notifications/discord/send"
    assert calls[1]["payload"]["target"] == "approvals"
    assert calls[1]["payload"]["dry_run"] is True
    assert "must-not-render" not in calls[1]["payload"]["content"]


def test_live_task_change_send_requires_explicit_selection():
    def fake_request(method, path, *, base_url, api_key, payload=None, timeout=15):
        return [sample_proposal()]

    with pytest.raises(ValueError) as exc:
        notify_pending_task_changes.send_task_change_notifications(
            base_url="http://127.0.0.1:8088",
            api_key="test-admin-key",
            dry_run=False,
            request_fn=fake_request,
        )

    assert "--proposal-id or --all" in str(exc.value)


def test_task_change_notifications_reject_non_approval_target():
    with pytest.raises(ValueError) as exc:
        notify_pending_task_changes.send_task_change_notifications(
            base_url="http://127.0.0.1:8088",
            api_key="test-admin-key",
            dry_run=True,
            target="alerts",
            request_fn=lambda *args, **kwargs: {},
        )

    assert "approvals channel" in str(exc.value)
