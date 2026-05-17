from __future__ import annotations

import json

import httpx
import pytest

from bridge.audit_client import ChannelAuditClient, build_channel_event, hash_identifier


def test_channel_event_hashes_identifiers_and_keeps_content_bounded_to_payload_fields():
    event = build_channel_event(
        channel_type="discord",
        status="blocked",
        channel_config_id="discord_home",
        channel_id="channel-1",
        author_id="user-1",
        message_id="message-1",
        request_preview=None,
        blocked_reason="unauthorized_user",
    )

    assert event["channel_id_hash"] == hash_identifier("channel-1")
    assert event["author_id_hash"] == hash_identifier("user-1")
    assert event["message_id"] == "message-1"
    assert event["request_preview"] is None
    assert event["blocked_reason"] == "unauthorized_user"
    assert "channel-1" not in json.dumps(event)
    assert "user-1" not in json.dumps(event)


@pytest.mark.asyncio
async def test_channel_audit_client_posts_with_automation_api_key():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-automation-api-key")
        seen["payload"] = json.loads(request.content.decode())
        return httpx.Response(201, json={"id": "evt-1", "status": "replied"})

    client = ChannelAuditClient(
        base_url="http://automation-api:8088",
        api_key="audit-key",
        transport=httpx.MockTransport(handler),
    )

    result = await client.record_event({"channel_type": "discord", "status": "replied"})

    assert result == {"id": "evt-1", "status": "replied"}
    assert seen["url"] == "http://automation-api:8088/channels/events"
    assert seen["key"] == "audit-key"
    assert seen["payload"]["status"] == "replied"


@pytest.mark.asyncio
async def test_channel_audit_client_is_disabled_without_key():
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    client = ChannelAuditClient(
        base_url="http://automation-api:8088",
        api_key="",
        transport=httpx.MockTransport(handler),
    )

    assert await client.record_event({"channel_type": "discord", "status": "replied"}) is None
    assert called is False
