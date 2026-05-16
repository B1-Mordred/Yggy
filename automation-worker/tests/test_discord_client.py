from __future__ import annotations

from worker.clients.discord_client import DiscordClient


def test_dry_run_discord_does_not_send_network_request(monkeypatch):
    def fail_post(*args, **kwargs):
        raise AssertionError("network send should not occur in dry-run")

    monkeypatch.setattr("worker.clients.discord_client.httpx.post", fail_post)
    client = DiscordClient(dry_run=True)
    result = client.send("briefings", "hello")
    assert result["sent"] is False
    assert result["dry_run"] is True
