from __future__ import annotations

from datetime import datetime, timezone

from worker.main import maybe_recover_stale_runs, maybe_run_retention, notification_decision, process_queued_runs, process_task


class FakeClient:
    def __init__(self) -> None:
        self.discord_calls: list[dict] = []
        self.completed_calls: list[dict] = []
        self.claim_calls: list[str] = []
        self.retention_calls = 0
        self.stale_recovery_calls = 0
        self.runs: list[dict] = []
        self.tasks: dict[str, dict] = {}

    def queue_run(self, task_id: str) -> dict:
        return {"run_id": f"run-{task_id}", "status": "queued_dry_run"}

    def claim_run(self, run_id: str) -> dict | None:
        self.claim_calls.append(run_id)
        if run_id == "claim-conflict":
            return None
        dry_run = run_id.startswith("run-") or run_id == "manual-dry-run-1"
        return {"id": run_id, "status": "running_dry_run" if dry_run else "running", "dry_run": dry_run}

    def get_task(self, task_id: str) -> dict:
        return self.tasks[task_id]

    def list_runs(self, task_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
        runs = self.runs
        if task_id:
            runs = [run for run in runs if run.get("task_id") == task_id]
        if status:
            runs = [run for run in runs if run.get("status") == status]
        return runs[:limit]

    def send_heartbeat(self, status: str = "ok", detail: dict | None = None) -> dict:
        return {"ok": True, "status": status, "detail": detail or {}}

    def run_retention(self) -> dict:
        self.retention_calls += 1
        return {"deleted": {"runs": 0, "audit_events": 0, "temporary_tasks": 0, "temporary_task_approvals": 0}}

    def recover_stale_runs(self) -> dict:
        self.stale_recovery_calls += 1
        return {"recovered_count": 0}

    def send_discord(self, target: str, content: str, dry_run: bool) -> dict:
        self.discord_calls.append({"target": target, "content": content, "dry_run": dry_run})
        return {"sent": False, "dry_run": dry_run, "target": target}

    def complete_run(self, run_id: str, status: str, log: dict) -> dict:
        self.completed_calls.append({"run_id": run_id, "status": status, "log": log})
        return {"id": run_id, "status": status}


def test_process_topic_digest_sends_discord_dry_run(monkeypatch):
    client = FakeClient()

    def fake_digest(config: dict) -> dict:
        return {"status": "dry_run", "message": "digest body", "items": []}

    monkeypatch.setattr("worker.main.run_topic_digest", fake_digest)

    result = process_task(
        client,
        {
            "enabled": True,
            "config": {
                "id": "daily_local_ai_security_briefing",
                "name": "Daily Local AI Security Briefing",
                "type": "topic_digest",
                "runtime": {"dry_run": True},
                "output": {"channel": "discord", "target": "briefings"},
                "notifications": {"on_empty_result": True},
            },
        },
    )

    assert result["status"] == "completed_dry_run"
    assert client.claim_calls == ["run-daily_local_ai_security_briefing"]
    assert client.discord_calls == [{"target": "briefings", "content": "digest body", "dry_run": True}]
    assert client.completed_calls[0]["run_id"] == "run-daily_local_ai_security_briefing"
    assert client.completed_calls[0]["status"] == "completed_dry_run"
    assert client.completed_calls[0]["log"]["notification_decision"]["classification"] == "empty"


def test_process_backup_verification_suppresses_clean_anomaly_only_result(monkeypatch):
    client = FakeClient()

    def fake_backup_verification(config: dict) -> dict:
        return {"status": "ok", "message": "backup ok", "notify": False, "failed_count": 0}

    monkeypatch.setattr("worker.main.run_backup_verification", fake_backup_verification)

    result = process_task(
        client,
        {
            "enabled": True,
            "config": {
                "id": "yggy_backup_verification",
                "name": "Yggy Backup Verification",
                "type": "backup_verification",
                "runtime": {"dry_run": True},
                "output": {"channel": "discord", "target": "alerts", "format": "anomalies only"},
                "notifications": {"on_success": False, "on_failure": True},
            },
        },
    )

    assert result["status"] == "completed_dry_run"
    assert client.discord_calls == []
    assert client.completed_calls[0]["log"]["result"]["status"] == "ok"
    assert client.completed_calls[0]["log"]["notification_decision"]["reason"] == "handler_suppressed"


def test_process_queued_run_uses_existing_run_and_sends_live(monkeypatch):
    client = FakeClient()
    task = {
        "id": "daily_local_ai_security_briefing",
        "enabled": True,
        "config": {
            "id": "daily_local_ai_security_briefing",
            "name": "Daily Local AI Security Briefing",
            "type": "topic_digest",
            "runtime": {"dry_run": False},
            "output": {"channel": "discord", "target": "briefings"},
            "notifications": {"on_empty_result": True},
        },
    }
    client.tasks = {task["id"]: task}
    client.runs = [
        {
            "id": "manual-run-1",
            "task_id": "daily_local_ai_security_briefing",
            "status": "queued",
            "completed_at": None,
        }
    ]

    def fake_digest(config: dict) -> dict:
        return {"status": "ready", "message": "live digest body", "items": []}

    monkeypatch.setattr("worker.main.run_topic_digest", fake_digest)

    processed = process_queued_runs(client)

    assert processed == {"daily_local_ai_security_briefing"}
    assert client.claim_calls == ["manual-run-1"]
    assert client.discord_calls == [{"target": "briefings", "content": "live digest body", "dry_run": False}]
    assert client.completed_calls[0]["run_id"] == "manual-run-1"
    assert client.completed_calls[0]["status"] == "completed"


def test_process_topic_digest_optionally_normalizes_with_n8n_before_discord(monkeypatch):
    client = FakeClient()
    n8n_calls = []

    def fake_digest(config: dict) -> dict:
        return {
            "status": "ready",
            "title": "Daily Local AI Security Briefing",
            "message": "live digest body",
            "items": [
                {
                    "title": "Open WebUI security release",
                    "summary": "Docker hardening update.",
                    "link": "https://example.com/open-webui",
                    "published": "2026-05-17",
                    "type": "rss",
                    "source_id": "open_webui_releases",
                    "source_name": "Open WebUI releases",
                    "source_trust_level": "official_project_release_feed",
                }
            ],
            "errors": [],
            "source_health": [
                {
                    "source": "open_webui_releases",
                    "source_id": "open_webui_releases",
                    "status": "ok",
                    "item_count": 1,
                    "trust_level": "official_project_release_feed",
                }
            ],
            "summary_mode": "deterministic",
        }

    def fake_n8n(config: dict, run_id: str, payload_override: dict) -> dict:
        n8n_calls.append({"config": config, "run_id": run_id, "payload": payload_override})
        return {
            "status": "ready",
            "notify": False,
            "webhook_id": "daily_briefing_stub",
            "response": {"action": "normalize_digest_payload", "normalized": {"item_count": 1}},
        }

    monkeypatch.setattr("worker.main.run_topic_digest", fake_digest)
    monkeypatch.setattr("worker.main.run_n8n_webhook", fake_n8n)

    task = {
        "id": "daily_local_ai_security_briefing",
        "enabled": True,
        "config": {
            "id": "daily_local_ai_security_briefing",
            "name": "Daily Local AI Security Briefing",
            "type": "topic_digest",
            "runtime": {"dry_run": False},
            "output": {"channel": "discord", "target": "briefings"},
            "notifications": {"on_empty_result": True},
            "n8n": {
                "webhook_id": "daily_briefing_stub",
                "path": "/webhook/yggy-daily-briefing",
                "method": "POST",
                "payload": {
                    "purpose": "daily_briefing_payload_normalizer",
                    "delivery_target": "briefings",
                },
            },
        },
    }
    client.tasks = {task["id"]: task}
    client.runs = [
        {
            "id": "manual-run-1",
            "task_id": "daily_local_ai_security_briefing",
            "status": "queued",
            "completed_at": None,
        }
    ]

    processed = process_queued_runs(client)

    assert processed == {"daily_local_ai_security_briefing"}
    assert n8n_calls[0]["run_id"] == "manual-run-1"
    assert n8n_calls[0]["payload"]["purpose"] == "daily_briefing_payload_normalizer"
    assert n8n_calls[0]["payload"]["delivery_target"] == "briefings"
    assert n8n_calls[0]["payload"]["title"] == "Daily Local AI Security Briefing"
    assert n8n_calls[0]["payload"]["summary"] == "live digest body"
    assert n8n_calls[0]["payload"]["items"][0]["url"] == "https://example.com/open-webui"
    assert n8n_calls[0]["payload"]["items"][0]["source_id"] == "open_webui_releases"
    assert n8n_calls[0]["payload"]["items"][0]["source_trust_level"] == "official_project_release_feed"
    assert n8n_calls[0]["payload"]["sources"] == ["https://example.com/open-webui"]
    assert n8n_calls[0]["payload"]["source_health"][0]["source_id"] == "open_webui_releases"
    assert client.discord_calls == [{"target": "briefings", "content": "live digest body", "dry_run": False}]
    assert client.completed_calls[0]["log"]["result"]["n8n"]["response"]["action"] == "normalize_digest_payload"


def test_queued_dry_run_preserves_dry_run_even_if_task_is_live(monkeypatch):
    client = FakeClient()
    task = {
        "id": "daily_local_ai_security_briefing",
        "enabled": True,
        "config": {
            "id": "daily_local_ai_security_briefing",
            "name": "Daily Local AI Security Briefing",
            "type": "topic_digest",
            "runtime": {"dry_run": False},
            "output": {"channel": "discord", "target": "briefings"},
            "notifications": {"on_empty_result": True},
        },
    }
    client.tasks = {task["id"]: task}
    client.runs = [
        {
            "id": "manual-dry-run-1",
            "task_id": "daily_local_ai_security_briefing",
            "status": "queued_dry_run",
            "completed_at": None,
        }
    ]

    def fake_digest(config: dict) -> dict:
        return {"status": "dry_run", "message": "dry digest body", "items": []}

    monkeypatch.setattr("worker.main.run_topic_digest", fake_digest)

    processed = process_queued_runs(client)

    assert processed == {"daily_local_ai_security_briefing"}
    assert client.claim_calls == ["manual-dry-run-1"]
    assert client.discord_calls == [{"target": "briefings", "content": "dry digest body", "dry_run": True}]
    assert client.completed_calls[0]["run_id"] == "manual-dry-run-1"
    assert client.completed_calls[0]["status"] == "completed_dry_run"


def test_process_queued_run_skips_claim_conflict(monkeypatch):
    client = FakeClient()
    client.tasks = {
        "daily_local_ai_security_briefing": {
            "id": "daily_local_ai_security_briefing",
            "enabled": True,
            "config": {
                "id": "daily_local_ai_security_briefing",
                "name": "Daily Local AI Security Briefing",
                "type": "topic_digest",
                "runtime": {"dry_run": False},
                "output": {"channel": "discord", "target": "briefings"},
            },
        }
    }
    client.runs = [
        {
            "id": "claim-conflict",
            "task_id": "daily_local_ai_security_briefing",
            "status": "queued",
            "completed_at": None,
        }
    ]

    def fake_digest(config: dict) -> dict:
        raise AssertionError("claimed-by-another-worker run should not execute")

    monkeypatch.setattr("worker.main.run_topic_digest", fake_digest)

    processed = process_queued_runs(client)

    assert processed == set()
    assert client.claim_calls == ["claim-conflict"]
    assert client.discord_calls == []
    assert client.completed_calls == []


def test_process_server_health_suppresses_discord_when_notify_false(monkeypatch):
    client = FakeClient()

    def fake_health(config: dict) -> dict:
        return {"status": "ok", "message": "No anomalies", "notify": False}

    monkeypatch.setattr("worker.main.run_server_health", fake_health)

    result = process_task(
        client,
        {
            "enabled": True,
            "config": {
                "id": "morning_server_health_check",
                "name": "Morning Server Health Check",
                "type": "server_health",
                "runtime": {"dry_run": True},
                "output": {"channel": "discord", "target": "alerts", "format": "anomalies only"},
            },
        },
    )

    assert result["status"] == "completed_dry_run"
    assert client.discord_calls == []
    assert client.completed_calls[0]["log"]["notification"] is None
    assert client.completed_calls[0]["log"]["notification_decision"]["reason"] == "handler_suppressed"


def test_process_n8n_webhook_records_internal_decision(monkeypatch):
    client = FakeClient()

    def fake_n8n(config: dict, run_id: str) -> dict:
        return {"status": "dry_run", "notify": False, "webhook_id": "daily_briefing_stub"}

    monkeypatch.setattr("worker.main.run_n8n_webhook", fake_n8n)

    result = process_task(
        client,
        {
            "enabled": True,
            "config": {
                "id": "daily_briefing_n8n_stub",
                "name": "Daily Briefing n8n Payload Normalizer",
                "type": "n8n_webhook",
                "runtime": {"dry_run": True},
                "output": {"channel": "internal", "target": "n8n"},
                "n8n": {
                    "webhook_id": "daily_briefing_stub",
                    "path": "/webhook/yggy-daily-briefing",
                    "method": "POST",
                },
            },
        },
    )

    assert result["status"] == "completed_dry_run"
    assert client.discord_calls == []
    assert client.completed_calls[0]["log"]["result"]["webhook_id"] == "daily_briefing_stub"
    assert client.completed_calls[0]["log"]["notification_decision"]["reason"] == "non_discord_output"


def test_success_notification_can_be_disabled(monkeypatch):
    client = FakeClient()

    def fake_digest(config: dict) -> dict:
        return {"status": "ready", "message": "digest body", "items": [{"title": "Item"}]}

    monkeypatch.setattr("worker.main.run_topic_digest", fake_digest)

    result = process_task(
        client,
        {
            "enabled": True,
            "config": {
                "id": "daily_local_ai_security_briefing",
                "name": "Daily Local AI Security Briefing",
                "type": "topic_digest",
                "runtime": {"dry_run": True},
                "output": {"channel": "discord", "target": "briefings"},
                "notifications": {"on_success": False},
            },
        },
    )

    assert result["status"] == "completed_dry_run"
    assert client.discord_calls == []
    assert client.completed_calls[0]["log"]["notification_decision"]["reason"] == "success_notifications_disabled"


def test_empty_result_notification_can_be_enabled(monkeypatch):
    client = FakeClient()

    def fake_digest(config: dict) -> dict:
        return {"status": "ready", "message": "digest body", "items": []}

    monkeypatch.setattr("worker.main.run_topic_digest", fake_digest)

    process_task(
        client,
        {
            "enabled": True,
            "config": {
                "id": "daily_local_ai_security_briefing",
                "name": "Daily Local AI Security Briefing",
                "type": "topic_digest",
                "runtime": {"dry_run": True},
                "output": {"channel": "discord", "target": "briefings"},
                "notifications": {"on_empty_result": True},
            },
        },
    )

    assert client.discord_calls == [{"target": "briefings", "content": "digest body", "dry_run": True}]
    assert client.completed_calls[0]["log"]["notification_decision"]["classification"] == "empty"


def test_quiet_hours_suppress_non_failure_notification():
    client = FakeClient()
    decision = notification_decision(
        client,
        {
            "id": "daily_local_ai_security_briefing",
            "trigger": {"timezone": "Europe/Berlin"},
            "notifications": {
                "quiet_hours": {
                    "enabled": True,
                    "start": "22:00",
                    "end": "07:00",
                    "timezone": "Europe/Berlin",
                }
            },
        },
        {"status": "ready", "items": [{"title": "Item"}]},
        run_id="run-1",
        now=datetime.fromisoformat("2026-05-17T23:30:00+02:00"),
    )

    assert decision["send"] is False
    assert decision["reason"] == "quiet_hours"


def test_failure_notification_ignores_quiet_hours(monkeypatch):
    client = FakeClient()

    def fake_health(config: dict) -> dict:
        return {"status": "degraded", "message": "bad health", "failed_count": 1, "notify": True}

    monkeypatch.setattr("worker.main.run_server_health", fake_health)

    process_task(
        client,
        {
            "enabled": True,
            "config": {
                "id": "morning_server_health_check",
                "name": "Morning Server Health Check",
                "type": "server_health",
                "runtime": {"dry_run": True},
                "output": {"channel": "discord", "target": "alerts"},
                "notifications": {
                    "quiet_hours": {
                        "enabled": True,
                        "start": "00:00",
                        "end": "23:59",
                        "timezone": "Europe/Berlin",
                    }
                },
            },
        },
    )

    assert client.discord_calls == [{"target": "alerts", "content": "bad health", "dry_run": True}]
    assert client.completed_calls[0]["log"]["notification_decision"]["classification"] == "failure"


def test_repeated_failure_notification_is_collapsed(monkeypatch):
    client = FakeClient()
    client.runs = [
        {
            "id": "previous-failure",
            "task_id": "morning_server_health_check",
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "log": {"notification_decision": {"classification": "failure"}},
        }
    ]

    def fake_health(config: dict) -> dict:
        return {"status": "degraded", "message": "bad health", "failed_count": 1, "notify": True}

    monkeypatch.setattr("worker.main.run_server_health", fake_health)

    process_task(
        client,
        {
            "enabled": True,
            "config": {
                "id": "morning_server_health_check",
                "name": "Morning Server Health Check",
                "type": "server_health",
                "runtime": {"dry_run": True},
                "output": {"channel": "discord", "target": "alerts"},
                "notifications": {"collapse_repeated_failures": True, "failure_collapse_window_minutes": 360},
            },
        },
    )

    assert client.discord_calls == []
    assert client.completed_calls[0]["log"]["notification_decision"]["reason"] == "repeated_failure_collapsed"


def test_handler_exception_can_send_failure_notification(monkeypatch):
    client = FakeClient()

    def fake_digest(config: dict) -> dict:
        raise RuntimeError("source parser broke")

    monkeypatch.setattr("worker.main.run_topic_digest", fake_digest)

    try:
        process_task(
            client,
            {
                "enabled": True,
                "config": {
                    "id": "daily_local_ai_security_briefing",
                    "name": "Daily Local AI Security Briefing",
                    "type": "topic_digest",
                    "runtime": {"dry_run": True},
                    "output": {"channel": "discord", "target": "alerts"},
                    "notifications": {"on_failure": True},
                },
            },
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("handler exception should be re-raised")

    assert client.discord_calls
    assert client.discord_calls[0]["target"] == "alerts"
    assert "Task failed" in client.discord_calls[0]["content"]
    assert client.completed_calls[0]["status"] == "failed"
    assert client.completed_calls[0]["log"]["notification_decision"]["classification"] == "failure"


def test_retention_runs_once_per_interval(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("AUTOMATION_RETENTION_INTERVAL_SECONDS", "60")
    monkeypatch.setattr("worker.main._LAST_RETENTION_RUN_AT", None)

    assert maybe_run_retention(client, now=100.0) is not None
    assert maybe_run_retention(client, now=120.0) is None
    assert maybe_run_retention(client, now=161.0) is not None
    assert client.retention_calls == 2


def test_retention_can_be_disabled(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("AUTOMATION_RETENTION_INTERVAL_SECONDS", "0")
    monkeypatch.setattr("worker.main._LAST_RETENTION_RUN_AT", None)

    assert maybe_run_retention(client, now=100.0) is None
    assert client.retention_calls == 0


def test_stale_run_recovery_runs_once_per_interval(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("AUTOMATION_STALE_RUN_RECOVERY_INTERVAL_SECONDS", "60")
    monkeypatch.setattr("worker.main._LAST_STALE_RUN_RECOVERY_AT", None)

    assert maybe_recover_stale_runs(client, now=100.0) is not None
    assert maybe_recover_stale_runs(client, now=120.0) is None
    assert maybe_recover_stale_runs(client, now=161.0) is not None
    assert client.stale_recovery_calls == 2


def test_stale_run_recovery_can_be_disabled(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("AUTOMATION_STALE_RUN_RECOVERY_INTERVAL_SECONDS", "0")
    monkeypatch.setattr("worker.main._LAST_STALE_RUN_RECOVERY_AT", None)

    assert maybe_recover_stale_runs(client, now=100.0) is None
    assert client.stale_recovery_calls == 0
