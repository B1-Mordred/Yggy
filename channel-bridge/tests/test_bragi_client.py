from __future__ import annotations

import json

import httpx
import pytest

from bridge.bragi_client import BragiClient, BragiClientError, build_discord_payload


@pytest.mark.asyncio
async def test_bragi_client_posts_discord_payload_with_bearer_key():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"reply": "hello", "classification": {"route": "general_chat"}})

    client = BragiClient(
        base_url="http://bragi:8650",
        api_key="bragi-key",
        transport=httpx.MockTransport(handler),
    )
    payload = build_discord_payload(channel_id="c1", author_id="u1", content="hello")

    result = await client.send_discord_message(payload)

    assert result["reply"] == "hello"
    assert seen["url"] == "http://bragi:8650/channels/discord/message"
    assert seen["authorization"] == "Bearer bragi-key"
    assert seen["payload"]["channel_id"] == "c1"
    assert seen["payload"]["author_id"] == "u1"


@pytest.mark.asyncio
async def test_bragi_client_raises_on_error_without_leaking_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="secret detail should not be surfaced")

    client = BragiClient(
        base_url="http://bragi:8650",
        api_key="bragi-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(BragiClientError) as exc:
        await client.send_discord_message(build_discord_payload(channel_id="c1", author_id="u1", content="hello"))

    assert "HTTP 403" in str(exc.value)
    assert "secret detail" not in str(exc.value)
