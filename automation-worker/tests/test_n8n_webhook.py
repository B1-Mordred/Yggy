from __future__ import annotations

import pytest

from worker.handlers.n8n_webhook import run_n8n_webhook


class FakeResponse:
    def __init__(self, status_code: int = 204, body=None, text: str = "") -> None:
        self.status_code = status_code
        self.body = body
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self.body is None:
            raise ValueError("no json body")
        return self.body


def n8n_config(dry_run: bool = True) -> dict:
    return {
        "id": "daily_briefing_n8n_stub",
        "name": "Daily Briefing n8n Payload Normalizer",
        "type": "n8n_webhook",
        "runtime": {"dry_run": dry_run, "timeout_seconds": 60},
        "n8n": {
            "webhook_id": "daily_briefing_stub",
            "path": "/webhook/yggy-daily-briefing",
            "method": "POST",
            "payload": {"purpose": "daily_briefing_stub"},
        },
    }


def test_n8n_webhook_dry_run_does_not_call_network():
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse()

    result = run_n8n_webhook(n8n_config(dry_run=True), run_id="run-1", http_post=fake_post)

    assert calls == []
    assert result["status"] == "dry_run"
    assert result["notify"] is False
    assert result["webhook_id"] == "daily_briefing_stub"


def test_n8n_webhook_live_requires_shared_secret(monkeypatch):
    monkeypatch.delenv("N8N_WEBHOOK_SHARED_SECRET", raising=False)

    with pytest.raises(ValueError, match="N8N_WEBHOOK_SHARED_SECRET"):
        run_n8n_webhook(n8n_config(dry_run=False), run_id="run-1")


def test_n8n_webhook_live_posts_to_internal_base_url(monkeypatch):
    calls = []
    monkeypatch.setenv("N8N_WEBHOOK_SHARED_SECRET", "test-shared-secret")
    monkeypatch.setenv("N8N_WEBHOOK_BASE_URL", "http://n8n:5678")

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(
            200,
            {
                "ok": True,
                "action": "normalize_digest_payload",
                "task_id": "daily_briefing_n8n_stub",
                "authorization": "do-not-log",
                "normalized": {"item_count": 0},
            },
        )

    result = run_n8n_webhook(n8n_config(dry_run=False), run_id="run-1", http_post=fake_post)

    assert result["status"] == "ready"
    assert result["status_code"] == 200
    assert result["response"]["ok"] is True
    assert result["response"]["action"] == "normalize_digest_payload"
    assert result["response"]["authorization"] == "<redacted>"
    assert result["response"]["normalized"] == {"item_count": 0}
    assert calls[0]["url"] == "http://n8n:5678/webhook/yggy-daily-briefing"
    assert calls[0]["headers"]["X-Yggy-Webhook-Token"] == "test-shared-secret"
    assert calls[0]["headers"]["X-Yggy-Run-Id"] == "run-1"
    assert calls[0]["json"]["payload"] == {"purpose": "daily_briefing_stub"}
