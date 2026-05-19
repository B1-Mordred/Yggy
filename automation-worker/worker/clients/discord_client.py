from __future__ import annotations

import httpx


class DiscordClient:
    def __init__(self, webhooks: dict[str, str] | None = None, dry_run: bool = True) -> None:
        self.webhooks = webhooks or {}
        self.dry_run = dry_run
        self.sent_payloads: list[dict] = []

    def send(self, target: str, content: str) -> dict:
        if self.dry_run:
            payload = {"sent": False, "dry_run": True, "target": target, "content_preview": content[:200]}
            self.sent_payloads.append(payload)
            return payload
        webhook = self.webhooks.get(target)
        if not webhook:
            raise ValueError(f"no webhook configured for target {target}")
        response = httpx.post(webhook, json={"content": content}, timeout=10)
        response.raise_for_status()
        return {"sent": True, "dry_run": False, "target": target, "status_code": response.status_code}
