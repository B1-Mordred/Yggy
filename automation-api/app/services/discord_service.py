from __future__ import annotations

import httpx

from app.config import get_settings


DISCORD_CONTENT_LIMIT = 2000


def split_discord_content(content: str, *, limit: int = DISCORD_CONTENT_LIMIT) -> list[str]:
    limit = max(5, min(limit, DISCORD_CONTENT_LIMIT))
    text = content.strip()
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


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

    @staticmethod
    def usable_webhook(value: str | None) -> bool:
        if not value or "replace-with" in value:
            return False
        return value.startswith("https://discord.com/api/webhooks/") or value.startswith(
            "https://discordapp.com/api/webhooks/"
        )

    def send(self, target: str, content: str, dry_run: bool | None = None) -> dict:
        effective_dry_run = self.dry_run if dry_run is None else dry_run
        chunks = split_discord_content(content)
        if effective_dry_run:
            return {
                "sent": False,
                "dry_run": True,
                "target": target,
                "content_preview": content[:200],
                "message_count": len(chunks),
            }

        webhook = self.webhooks.get(target)
        if self.usable_webhook(webhook):
            status_codes = []
            for chunk in chunks:
                response = httpx.post(webhook, json={"content": chunk}, timeout=10)
                response.raise_for_status()
                status_codes.append(response.status_code)
            return {
                "sent": True,
                "dry_run": False,
                "target": target,
                "transport": "webhook",
                "status_code": status_codes[-1],
                "status_codes": status_codes,
                "message_count": len(chunks),
            }

        channel_id = self.channels.get(target)
        if not self.bot_token or not channel_id:
            raise ValueError(f"no Discord webhook or bot channel configured for target {target}")

        status_codes = []
        for chunk in chunks:
            response = httpx.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {self.bot_token}"},
                json={
                    "content": chunk,
                    "allowed_mentions": {"parse": []},
                },
                timeout=10,
            )
            response.raise_for_status()
            status_codes.append(response.status_code)
        return {
            "sent": True,
            "dry_run": False,
            "target": target,
            "transport": "bot",
            "status_code": status_codes[-1],
            "status_codes": status_codes,
            "message_count": len(chunks),
        }
