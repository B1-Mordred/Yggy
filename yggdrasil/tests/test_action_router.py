from __future__ import annotations

import yggdrasil_action_api


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

