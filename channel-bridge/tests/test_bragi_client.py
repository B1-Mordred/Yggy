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


@pytest.mark.asyncio
async def test_bragi_client_fetches_and_marks_followups():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers.get("authorization"), json.loads(request.content.decode() or "{}") if request.content else None))
        if request.method == "GET":
            return httpx.Response(200, json={"followups": [{"intake_id": "bragi_intake_1", "message": "finish me"}]})
        return httpx.Response(200, json={"status": "marked_sent"})

    client = BragiClient(
        base_url="http://bragi:8650",
        api_key="bragi-key",
        transport=httpx.MockTransport(handler),
    )

    followups = await client.pending_followups(user_id="local_user", channel="discord", limit=3)
    marked = await client.mark_followup_sent(user_id="local_user", intake_id="bragi_intake_1")

    assert followups == [{"intake_id": "bragi_intake_1", "message": "finish me"}]
    assert marked["status"] == "marked_sent"
    assert seen[0][0] == "GET"
    assert "user_id=local_user" in seen[0][1]
    assert "channel=discord" in seen[0][1]
    assert seen[0][2] == "Bearer bragi-key"
    assert seen[1][0] == "POST"
    assert seen[1][3] == {"user_id": "local_user", "intake_id": "bragi_intake_1"}
