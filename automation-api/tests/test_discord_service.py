from __future__ import annotations

from app.services.discord_service import DiscordService


def test_discord_service_uses_bot_channel_when_no_webhook(monkeypatch):
    monkeypatch.setenv("DISCORD_DRY_RUN", "false")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_CHANNEL_BRIEFINGS", "12345")
    monkeypatch.delenv("DISCORD_WEBHOOK_BRIEFINGS", raising=False)

    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("app.services.discord_service.httpx.post", fake_post)

    result = DiscordService().send("briefings", "hello")

    assert result["sent"] is True
    assert result["transport"] == "bot"
    assert calls[0]["url"] == "https://discord.com/api/v10/channels/12345/messages"
    assert calls[0]["headers"] == {"Authorization": "Bot test-token"}
    assert calls[0]["json"] == {"content": "hello", "allowed_mentions": {"parse": []}}


def test_discord_service_ignores_placeholder_webhook_and_uses_bot(monkeypatch):
    monkeypatch.setenv("DISCORD_DRY_RUN", "false")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_CHANNEL_BRIEFINGS", "12345")
    monkeypatch.setenv("DISCORD_WEBHOOK_BRIEFINGS", "replace-with-discord-webhook-url")

    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("app.services.discord_service.httpx.post", fake_post)

    result = DiscordService().send("briefings", "hello")

    assert result["sent"] is True
    assert result["transport"] == "bot"
    assert calls[0]["url"] == "https://discord.com/api/v10/channels/12345/messages"
