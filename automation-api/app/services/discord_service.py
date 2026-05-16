from __future__ import annotations

import httpx

from app.config import get_settings


class DiscordService:
    def __init__(self) -> None:
        settings = get_settings()
        self.dry_run = settings.discord_dry_run
        self.webhooks = {
            "briefings": settings.discord_webhook_briefings,
            "alerts": settings.discord_webhook_alerts,
            "approvals": settings.discord_webhook_approvals,
        }

    def send(self, target: str, content: str, dry_run: bool | None = None) -> dict:
        effective_dry_run = self.dry_run if dry_run is None else dry_run
        if effective_dry_run:
            return {"sent": False, "dry_run": True, "target": target, "content_preview": content[:200]}

        webhook = self.webhooks.get(target)
        if not webhook:
            raise ValueError(f"no webhook configured for target {target}")

        response = httpx.post(webhook, json={"content": content}, timeout=10)
        response.raise_for_status()
        return {"sent": True, "dry_run": False, "target": target, "status_code": response.status_code}
