from __future__ import annotations

import httpx

from app.config import get_settings


class DiscordService:
    def __init__(self) -> None:
        settings = get_settings()
        self.dry_run = settings.discord_dry_run
        self.bot_token = settings.discord_bot_token
        fallback_channel = settings.discord_home_channel
        self.webhooks = {
            "briefings": settings.discord_webhook_briefings,
            "alerts": settings.discord_webhook_alerts,
            "approvals": settings.discord_webhook_approvals,
        }
        self.channels = {
            "briefings": settings.discord_channel_briefings or fallback_channel,
            "alerts": settings.discord_channel_alerts or fallback_channel,
            "approvals": settings.discord_channel_approvals or fallback_channel,
        }

    def send(self, target: str, content: str, dry_run: bool | None = None) -> dict:
        effective_dry_run = self.dry_run if dry_run is None else dry_run
        if effective_dry_run:
            return {"sent": False, "dry_run": True, "target": target, "content_preview": content[:200]}

        webhook = self.webhooks.get(target)
        if webhook:
            response = httpx.post(webhook, json={"content": content[:2000]}, timeout=10)
            response.raise_for_status()
            return {"sent": True, "dry_run": False, "target": target, "transport": "webhook", "status_code": response.status_code}

        channel_id = self.channels.get(target)
        if not self.bot_token or not channel_id:
            raise ValueError(f"no Discord webhook or bot channel configured for target {target}")

        response = httpx.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {self.bot_token}"},
            json={
                "content": content[:2000],
                "allowed_mentions": {"parse": []},
            },
            timeout=10,
        )
        response.raise_for_status()
        return {"sent": True, "dry_run": False, "target": target, "transport": "bot", "status_code": response.status_code}
