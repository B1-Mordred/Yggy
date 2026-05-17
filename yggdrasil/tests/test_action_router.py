from __future__ import annotations

import yggdrasil_action_api


def sample_run(**overrides):
    run = {
        "id": "275b12e1-ae83-4133-b0ce-f7401249ae17",
        "task_id": "daily_local_ai_security_briefing",
        "status": "completed",
        "created_at": "2026-05-16T22:12:16",
        "completed_at": "2026-05-16T22:12:56",
        "log": {
            "notification": {"sent": True, "dry_run": False, "transport": "bot", "status_code": 200},
            "result": {
                "summary_mode": "llm",
                "items": [{"title": "Item"} for _ in range(10)],
                "source_count": 5,
                "approved_source_count": 4,
                "source_health": [
                    {"source": "open_webui_releases", "status": "ok"},
                    {"source": "ollama_releases", "status": "ok"},
                    {"source": "n8n_releases", "status": "error"},
                    {"source": "old_source", "status": "blocked"},
                ],
                "errors": [],
            },
        },
    }
    run.update(overrides)
    return run


def sample_backup_run(**overrides):
    run = sample_run(
        id="33333333-3333-3333-3333-333333333333",
        task_id="yggy_backup_verification",
        status="completed_dry_run",
        log={
            "notification": None,
            "result": {
                "status": "ok",
                "notify": False,
                "backup_count": 30,
                "latest_backup": {
                    "name": "yggy-20260517-130725Z",
                    "age_hours": 0.2,
                    "mysql_dump_bytes": 214689,
                },
                "restore_dry_run": {"ok": True},
                "secret_scan": {"status": "clean", "potential_secret_file_count": 0, "files": []},
                "failed_count": 0,
                "anomalies": [],
            },
        },
    )
    run.update(overrides)
    return run


def test_send_daily_brief_now_queues_automation_run(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 202, {"run_id": "manual-run-1", "status": "queued"}

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "send daily brief now"}],
    )

    assert calls == [("POST", "/tasks/daily_local_ai_security_briefing/run")]
    assert "Run queued" in answer
    assert "daily_local_ai_security_briefing" in answer


def test_send_daily_brief_now_reports_rate_limit(monkeypatch):
    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        return 202, {
            "run_id": None,
            "status": "rate_limited",
            "queued": False,
            "deduplicated": True,
            "reason": "min_seconds_between_runs",
            "retry_after_seconds": 240,
        }

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "send daily brief now"}],
    )

    assert "Run not queued" in answer
    assert "min_seconds_between_runs" in answer
    assert "Retry after: `240s`" in answer
    assert "Existing run" not in answer


def test_local_ai_security_draft_includes_run_safety_limits():
    draft = yggdrasil_action_api.local_ai_security_briefing_draft(
        "draft a weekday 08:00 local AI security briefing"
    )

    assert draft["policy"]["max_runs_per_hour"] == 3
    assert draft["policy"]["max_runs_per_day"] == 10
    assert draft["policy"]["min_seconds_between_runs"] == 300


def test_list_task_templates_uses_automation_api(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, [
            {
                "id": "topic_digest",
                "name": "Topic Digest",
                "task_type": "topic_digest",
                "default_approval_level": "L1_NOTIFY_ONLY",
                "allowed_output_targets": ["briefings", "alerts"],
            },
            {
                "id": "server_health",
                "name": "Server Health Check",
                "task_type": "server_health",
                "default_approval_level": "L1_NOTIFY_ONLY",
                "allowed_output_targets": ["alerts"],
            },
        ]

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "list task templates"}],
    )

    assert calls == [("GET", "/task-templates")]
    assert "Task templates:" in answer
    assert "`topic_digest`" in answer
    assert "`server_health`" in answer
    assert "disabled dry-run scaffolds" in answer


def test_show_task_template_details(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, {
            "id": "backup_verification",
            "name": "Backup Verification",
            "task_type": "backup_verification",
            "default_approval_level": "L1_NOTIFY_ONLY",
            "allowed_output_targets": ["alerts"],
            "description": "Draft a read-only backup verification task.",
        }

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "show the backup verification template"}],
    )

    assert calls == [("GET", "/task-templates/backup_verification")]
    assert "Task template `backup_verification`" in answer
    assert "Default approval: `L1_NOTIFY_ONLY`" in answer
    assert "does not approve, enable, or run a task" in answer


def test_draft_daily_brief_uses_template_endpoint(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        return 201, {
            "task": {
                "id": "daily_local_ai_security_briefing",
                "name": "Daily Local AI Security Briefing",
                "type": "topic_digest",
                "enabled": False,
                "status": "pending_approval",
                "approval_level": "L1_NOTIFY_ONLY",
                "config": {
                    "trigger": {"cron": "0 8 * * 1-5", "timezone": "Europe/Berlin"},
                    "output": {"channel": "discord", "target": "briefings"},
                    "runtime": {"dry_run": True},
                    "policy": {"allow_shell": False, "allow_docker_socket": False},
                },
            },
            "approval": {"id": "approval-1", "approval_level": "L1_NOTIFY_ONLY", "status": "pending"},
            "rendered_config": {
                "id": "daily_local_ai_security_briefing",
                "name": "Daily Local AI Security Briefing",
                "type": "topic_digest",
                "enabled": False,
                "runtime": {"dry_run": True},
                "policy": {"allow_shell": False, "allow_docker_socket": False},
            },
        }

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "draft a weekday 08:00 local AI security briefing to Discord"}],
    )

    assert calls[0][0:2] == ("POST", "/task-templates/topic_digest/draft")
    assert calls[0][2]["cron"] == "0 8 * * 1-5"
    assert calls[0][2]["source_ids"] == ["open_webui_releases", "ollama_releases", "n8n_releases", "docker_blog"]
    assert "from template `topic_digest`" in answer
    assert "Approval request created" in answer


def test_canonical_action_drafts_from_template(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        return 201, {
            "task": {
                "id": "daily_ai_stack_health",
                "name": "Daily AI Stack Health Check",
                "type": "server_health",
                "enabled": False,
                "status": "pending_approval",
                "approval_level": "L1_NOTIFY_ONLY",
                "config": {
                    "trigger": {"cron": "0 8 * * *", "timezone": "Europe/Berlin"},
                    "output": {"channel": "discord", "target": "alerts"},
                    "runtime": {"dry_run": True},
                    "policy": {"allow_shell": False, "allow_docker_socket": False},
                },
            },
            "approval": {"id": "approval-1", "approval_level": "L1_NOTIFY_ONLY", "status": "pending"},
            "rendered_config": {
                "id": "daily_ai_stack_health",
                "name": "Daily AI Stack Health Check",
                "type": "server_health",
                "enabled": False,
                "runtime": {"dry_run": True},
                "policy": {"allow_shell": False, "allow_docker_socket": False},
            },
        }

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    status_code, body = yggdrasil_action_api.handle_canonical_action(
        {
            "action": "draft_task_from_template",
            "capability_id": "server_health.v1",
            "template_id": "server_health",
            "template_values": {
                "id": "daily_ai_stack_health",
                "name": "Daily AI Stack Health Check",
                "cron": "0 8 * * *",
                "timezone": "Europe/Berlin",
                "output_target": "alerts",
                "check_ids": ["automation_api"],
            },
        }
    )

    assert status_code == 200
    assert body["status"] == "ok"
    assert calls == [
        (
            "POST",
            "/task-templates/server_health/draft",
            {
                "id": "daily_ai_stack_health",
                "name": "Daily AI Stack Health Check",
                "cron": "0 8 * * *",
                "timezone": "Europe/Berlin",
                "output_target": "alerts",
                "check_ids": ["automation_api"],
            },
        )
    ]
    assert "Draft task `daily_ai_stack_health`" in body["answer"]


def test_canonical_action_rejects_raw_natural_language():
    status_code, body = yggdrasil_action_api.handle_canonical_action(
        {
            "action": "draft_task_from_template",
            "capability_id": "server_health.v1",
            "template_id": "server_health",
            "template_values": {"raw_text": "Can you keep an eye on my AI server?"},
        }
    )

    assert status_code == 422
    assert "raw natural language" in body["detail"]


def test_canonical_action_lists_tasks(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, [
            {
                "id": "daily_local_ai_security_briefing",
                "name": "Daily Local AI Security Briefing",
                "type": "topic_digest",
                "enabled": True,
                "status": "enabled",
                "approval_level": "L1_NOTIFY_ONLY",
            }
        ]

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    status_code, body = yggdrasil_action_api.handle_canonical_action({"action": "list_tasks"})

    assert status_code == 200
    assert calls == [("GET", "/tasks")]
    assert body["status"] == "ok"
    assert "Automation tasks:" in body["answer"]


def test_canonical_action_runs_task(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 202, {"run_id": "manual-run-1", "status": "queued"}

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    status_code, body = yggdrasil_action_api.handle_canonical_action(
        {"action": "run_task", "task_id": "daily_local_ai_security_briefing"}
    )

    assert status_code == 200
    assert calls == [("POST", "/tasks/daily_local_ai_security_briefing/run")]
    assert body["status"] == "ok"
    assert "Run queued" in body["answer"]


def test_canonical_action_rejects_invalid_task_id():
    status_code, body = yggdrasil_action_api.handle_canonical_action({"action": "run_task", "task_id": "../bad"})

    assert status_code == 422
    assert "task_id must be slug-like" in body["detail"]


def test_schedule_change_creates_task_change_proposal(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []
    task_config = yggdrasil_action_api.local_ai_security_briefing_draft("draft weekday 08:00 brief")
    task_config["enabled"] = True
    task = {
        "id": "daily_local_ai_security_briefing",
        "name": "Daily Local AI Security Briefing",
        "type": "topic_digest",
        "enabled": True,
        "status": "enabled",
        "approval_level": "L1_NOTIFY_ONLY",
        "config": task_config,
    }

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/tasks/daily_local_ai_security_briefing":
            return 200, task
        if method == "POST" and path == "/tasks/daily_local_ai_security_briefing/propose-change":
            return 201, {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "task_id": "daily_local_ai_security_briefing",
                "status": "pending",
                "approval_level": "L1_NOTIFY_ONLY",
                "summary": "schedule change",
                "nonce": "nonce-1",
                "risk": {"severity": "operator_review", "categories": {"schedule": ["trigger.cron"]}},
                "diff": {
                    "counts": {"changed": 1, "added": 0, "removed": 0},
                    "changed": [{"path": "trigger.cron", "before": "0 8 * * 1-5", "after": "30 7 * * 1-5"}],
                },
            }
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "change the daily briefing schedule to 07:30 weekdays"}],
    )

    assert calls[0][0:2] == ("GET", "/tasks/daily_local_ai_security_briefing")
    assert calls[1][0:2] == ("POST", "/tasks/daily_local_ai_security_briefing/propose-change")
    assert calls[1][2]["proposed_config"]["trigger"]["cron"] == "30 7 * * 1-5"
    assert "Task change proposal created" in answer
    assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in answer
    assert "Nonce: `nonce-1`" in answer


def test_list_pending_task_change_proposals(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, [
            {
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "task_id": "daily_local_ai_security_briefing",
                "status": "pending",
                "risk": {"severity": "operator_review"},
            }
        ]

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "show pending task change proposals"}],
    )

    assert calls == [("GET", "/task-change-proposals?limit=20&status=pending")]
    assert "Task change proposals:" in answer
    assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in answer


def test_show_latest_daily_brief_run(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, [sample_run()]

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "show the latest daily brief run"}],
    )

    assert calls == [("GET", "/runs?task_id=daily_local_ai_security_briefing&limit=1")]
    assert "Run `275b12e1-ae83-4133-b0ce-f7401249ae17`" in answer
    assert "Delivery: sent via bot" in answer
    assert "Summary mode: `llm`" in answer
    assert "Items: `10`" in answer
    assert "Approved sources: `4`" in answer
    assert "Source health: 2 ok, 1 failed, 1 blocked" in answer


def test_did_daily_brief_send(monkeypatch):
    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        return 200, [sample_run()]

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "did the daily brief send?"}],
    )

    assert "Delivery: sent via bot" in answer
    assert "Dry run: `false`" in answer


def test_show_failed_automation_runs(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, [sample_run(id="11111111-1111-1111-1111-111111111111", status="failed", log={"message": "boom"})]

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "show failed automation runs"}],
    )

    assert calls == [("GET", "/runs?status=failed&limit=5")]
    assert "Failed automation runs:" in answer
    assert "`11111111-1111-1111-1111-111111111111` `failed`" in answer


def test_show_specific_run(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, sample_run()

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "show run 275b12e1-ae83-4133-b0ce-f7401249ae17"}],
    )

    assert calls == [("GET", "/runs/275b12e1-ae83-4133-b0ce-f7401249ae17")]
    assert "Run `275b12e1-ae83-4133-b0ce-f7401249ae17`" in answer


def test_run_server_health_check_now(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 202, {"run_id": "health-run-1", "status": "queued_dry_run"}

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "run server health check now"}],
    )

    assert calls == [("POST", "/tasks/morning_server_health_check/run")]
    assert "Run queued" in answer
    assert "morning_server_health_check" in answer


def test_run_backup_verification_now(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 202, {"run_id": "backup-run-1", "status": "queued_dry_run"}

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "run backup verification now"}],
    )

    assert calls == [("POST", "/tasks/yggy_backup_verification/run")]
    assert "Run queued" in answer
    assert "yggy_backup_verification" in answer


def test_show_latest_backup_verification_run(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, [sample_backup_run()]

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "show latest backup check"}],
    )

    assert calls == [("GET", "/runs?task_id=yggy_backup_verification&limit=1")]
    assert "Backup verification: `ok`" in answer
    assert "Latest backup: `yggy-20260517-130725Z`" in answer
    assert "Secret scan: `clean`" in answer
    assert "alert suppressed" in answer


def test_show_server_health_uses_latest_run(monkeypatch):
    calls: list[tuple[str, str]] = []
    health_run = sample_run(
        id="22222222-2222-2222-2222-222222222222",
        task_id="morning_server_health_check",
        log={
            "notification": None,
            "result": {
                "status": "ok",
                "notify": False,
                "ok_count": 2,
                    "failed_count": 1,
                "checks": [
                    {"name": "automation_api", "type": "http_health", "ok": True, "status_code": 200, "latency_ms": 8},
                    {"name": "automation_worker", "type": "worker_heartbeat", "ok": True, "worker_age_seconds": 0},
                    {
                        "name": "yggy_metrics_exporter",
                        "type": "service_metrics",
                        "ok": False,
                        "metrics_failed_count": 1,
                        "metrics_failed_services": ["open_webui"],
                    },
                ],
            },
        },
    )

    def fake_automation_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        return 200, [health_run]

    monkeypatch.setattr(yggdrasil_action_api, "automation_request", fake_automation_request)

    answer = yggdrasil_action_api.route_chat(
        [{"role": "user", "content": "show server health"}],
    )

    assert calls == [("GET", "/runs?task_id=morning_server_health_check&limit=1")]
    assert "Run `22222222-2222-2222-2222-222222222222`" in answer
    assert "morning_server_health_check" in answer
    assert "Health: `ok`" in answer
    assert "Checks: `2/3 ok`, failed `1`" in answer
    assert "failed services: open_webui" in answer
    assert "Items:" not in answer
