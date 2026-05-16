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
