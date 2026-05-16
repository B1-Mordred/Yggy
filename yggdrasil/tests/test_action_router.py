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
                "errors": [],
            },
        },
    }
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
                "failed_count": 0,
                "checks": [
                    {"name": "automation_api", "type": "http_health", "ok": True, "status_code": 200, "latency_ms": 8},
                    {"name": "automation_worker", "type": "worker_heartbeat", "ok": True, "worker_age_seconds": 0},
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
    assert "Checks: `2/2 ok`, failed `0`" in answer
    assert "Items:" not in answer
