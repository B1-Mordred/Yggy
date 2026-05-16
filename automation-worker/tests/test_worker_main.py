from __future__ import annotations

from worker.main import process_queued_runs, process_task


class FakeClient:
    def __init__(self) -> None:
        self.discord_calls: list[dict] = []
        self.completed_calls: list[dict] = []
        self.claim_calls: list[str] = []
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

    def list_runs(self) -> list[dict]:
        return self.runs

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
    assert client.claim_calls == ["run-daily_local_ai_security_briefing"]
    assert client.discord_calls == [{"target": "briefings", "content": "digest body", "dry_run": True}]
    assert client.completed_calls[0]["run_id"] == "run-daily_local_ai_security_briefing"
    assert client.completed_calls[0]["status"] == "completed_dry_run"


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
