from __future__ import annotations

from typing import Any

import httpx


class BragiClientError(RuntimeError):
    """Raised when Bragi rejects or fails a channel bridge request."""


class BragiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    async def send_discord_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/channels/discord/message",
                headers=headers,
                json=payload,
            )
        if response.status_code >= 400:
            raise BragiClientError(f"Bragi returned HTTP {response.status_code}")
        data = response.json() if response.content else {}
        if not isinstance(data, dict):
            raise BragiClientError("Bragi returned a non-object response")
        return data

    async def pending_followups(self, *, user_id: str, channel: str = "discord", limit: int = 10) -> list[dict[str, Any]]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.get(
                f"{self.base_url}/intakes/pending-followups",
                headers=headers,
                params={"user_id": user_id, "channel": channel, "limit": limit},
            )
        if response.status_code >= 400:
            raise BragiClientError(f"Bragi returned HTTP {response.status_code}")
        data = response.json() if response.content else {}
        followups = data.get("followups") if isinstance(data, dict) else None
        if not isinstance(followups, list):
            raise BragiClientError("Bragi returned invalid followup data")
        return [item for item in followups if isinstance(item, dict)]

    async def mark_followup_sent(self, *, user_id: str, intake_id: str) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/intakes/followups/mark-sent",
                headers=headers,
                json={"user_id": user_id, "intake_id": intake_id},
            )
        if response.status_code >= 400:
            raise BragiClientError(f"Bragi returned HTTP {response.status_code}")
        data = response.json() if response.content else {}
        if not isinstance(data, dict):
            raise BragiClientError("Bragi returned a non-object response")
        return data


def build_discord_payload(
    *,
    channel_id: str,
    author_id: str,
    content: str,
    author_name: str | None = None,
    message_id: str | None = None,
    timestamp: str | None = None,
    is_bot: bool = False,
    attachments: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "author_id": author_id,
        "author_name": author_name,
        "content": content,
        "message_id": message_id,
        "timestamp": timestamp,
        "is_bot": is_bot,
        "attachments": attachments or [],
        "history": history or [],
    }
