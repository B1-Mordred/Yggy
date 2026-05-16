from __future__ import annotations

from worker.main import process_task


class FakeClient:
    def __init__(self) -> None:
        self.discord_calls: list[dict] = []
        self.completed_calls: list[dict] = []

    def queue_run(self, task_id: str) -> dict:
        return {"run_id": f"run-{task_id}", "status": "queued_dry_run"}

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
            },
        },
    )

    assert result["status"] == "completed_dry_run"
    assert client.discord_calls == [{"target": "briefings", "content": "digest body", "dry_run": True}]
    assert client.completed_calls[0]["run_id"] == "run-daily_local_ai_security_briefing"
    assert client.completed_calls[0]["status"] == "completed_dry_run"
