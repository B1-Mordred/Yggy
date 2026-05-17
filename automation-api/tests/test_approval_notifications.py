from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import notify_pending_approvals  # noqa: E402


def sample_approval(**overrides):
    approval = {
        "id": "approval-123",
        "task_id": "daily_local_ai_security_briefing",
        "approval_level": "L1_NOTIFY_ONLY",
        "requested_by": "yggdrasil",
        "risk": "L1_NOTIFY_ONLY",
        "status": "pending",
        "created_at": "2026-05-17T13:00:00Z",
        "summary": "Approval requested for task daily_local_ai_security_briefing",
        "nonce_hash": "must-not-render",
        "nonce": "must-not-render",
        "admin_key": "must-not-render",
        "task": {
            "id": "daily_local_ai_security_briefing",
            "name": "Daily Local AI Security Briefing",
            "type": "topic_digest",
            "enabled": False,
            "status": "pending_approval",
            "trigger": {"cron": "0 8 * * 1-5", "timezone": "Europe/Berlin"},
            "output": {"channel": "discord", "target": "briefings"},
            "runtime": {"dry_run": False},
        },
        "review": {
            "actions": [
                "Enable task daily_local_ai_security_briefing after approval",
                "Use live Discord delivery to whitelisted target briefings",
            ],
            "failure_mode": "A noisy message could be sent to the whitelisted Discord target.",
            "config_diff": {
                "version": 2,
                "change_type": "update",
                "diff": {
                    "counts": {"added": 0, "removed": 0, "changed": 2},
                    "changed": [
                        {"path": "enabled", "before": False, "after": True},
                        {"path": "runtime.dry_run", "before": True, "after": False},
                    ],
                    "added": [],
                    "removed": [],
                    "truncated": False,
                },
            },
        },
    }
    approval.update(overrides)
    return approval


def test_render_approval_notification_excludes_nonce_and_admin_material():
    message = notify_pending_approvals.render_approval_notification(sample_approval())

    assert "Approval requested" in message
    assert "daily_local_ai_security_briefing" in message
    assert "Nonce: not included" in message
    assert "must-not-render" not in message
    assert "nonce_hash" not in message
    assert "admin_key" not in message
    assert "Changed `runtime.dry_run`" in message


def test_selected_pending_approvals_filters_by_id():
    status = {"pending_approvals": [sample_approval(id="one"), sample_approval(id="two")]}

    selected = notify_pending_approvals.selected_pending_approvals(status, approval_id="two")

    assert [item["id"] for item in selected] == ["two"]


def test_send_approval_notifications_dry_run_posts_to_approvals_target():
    calls = []

    def fake_request(method, path, *, base_url, api_key, payload=None, timeout=15):
        calls.append({"method": method, "path": path, "payload": payload})
        if path == "/ops/status":
            return {"pending_approvals": [sample_approval()]}
        if path == "/notifications/discord/send":
            return {"sent": False, "dry_run": payload["dry_run"], "target": payload["target"]}
        raise AssertionError(path)

    result = notify_pending_approvals.send_approval_notifications(
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


def test_live_send_requires_explicit_selection():
    def fake_request(method, path, *, base_url, api_key, payload=None, timeout=15):
        return {"pending_approvals": [sample_approval()]}

    with pytest.raises(ValueError) as exc:
        notify_pending_approvals.send_approval_notifications(
            base_url="http://127.0.0.1:8088",
            api_key="test-admin-key",
            dry_run=False,
            request_fn=fake_request,
        )

    assert "--approval-id or --all" in str(exc.value)


def test_approval_notifications_reject_non_approval_target():
    with pytest.raises(ValueError) as exc:
        notify_pending_approvals.send_approval_notifications(
            base_url="http://127.0.0.1:8088",
            api_key="test-admin-key",
            dry_run=True,
            target="alerts",
            request_fn=lambda *args, **kwargs: {},
        )

    assert "approvals channel" in str(exc.value)
