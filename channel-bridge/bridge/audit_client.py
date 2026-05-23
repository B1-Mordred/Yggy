from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


class ChannelAuditClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def record_event(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        headers = {
            "Content-Type": "application/json",
            "X-Automation-Api-Key": self.api_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f"{self.base_url}/channels/events", headers=headers, json=payload)
        if response.status_code >= 400:
            logger.warning("channel audit event rejected by automation-api: HTTP %s", response.status_code)
            return None
        data = response.json() if response.content else {}
        return data if isinstance(data, dict) else None

    async def pending_notifications(
        self,
        *,
        channel: str,
        user_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        headers = {"X-Automation-Api-Key": self.api_key}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.get(
                f"{self.base_url}/channels/notifications/pending",
                headers=headers,
                params={"channel": channel, "user_id": user_id, "limit": limit},
            )
        if response.status_code >= 400:
            logger.warning("channel notifications rejected by automation-api: HTTP %s", response.status_code)
            return []
        data = response.json() if response.content else {}
        notifications = data.get("notifications") if isinstance(data, dict) else None
        return [item for item in notifications if isinstance(item, dict)] if isinstance(notifications, list) else []

    async def mark_notification(
        self,
        *,
        notification_id: str,
        status: str,
        error: str = "",
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        headers = {
            "Content-Type": "application/json",
            "X-Automation-Api-Key": self.api_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/channels/notifications/{notification_id}/mark",
                headers=headers,
                json={"status": status, "error": error},
            )
        if response.status_code >= 400:
            logger.warning("channel notification mark rejected by automation-api: HTTP %s", response.status_code)
            return None
        data = response.json() if response.content else {}
        return data if isinstance(data, dict) else None


def hash_identifier(value: str | int | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_channel_event(
    *,
    channel_type: str,
    status: str,
    channel_config_id: str | None = None,
    channel_id: str | int | None = None,
    author_id: str | int | None = None,
    message_id: str | int | None = None,
    request_preview: str | None = None,
    route: str | None = None,
    required_capability: str | None = None,
    forwarded_to_yggdrasil: bool = False,
    blocked_reason: str | None = None,
    reply_preview: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "channel_type": channel_type,
        "channel_config_id": channel_config_id,
        "channel_id_hash": hash_identifier(channel_id),
        "author_id_hash": hash_identifier(author_id),
        "message_id": str(message_id) if message_id is not None else None,
        "request_preview": request_preview,
        "route": route,
        "required_capability": required_capability,
        "forwarded_to_yggdrasil": forwarded_to_yggdrasil,
        "status": status,
        "blocked_reason": blocked_reason,
        "reply_preview": reply_preview,
        "metadata": metadata or {},
    }
