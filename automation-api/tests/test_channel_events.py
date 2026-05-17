from __future__ import annotations

from conftest import ADMIN_HEADERS, CHANNEL_BRIDGE_HEADERS, TOOL_HEADERS


def test_channel_bridge_key_can_create_redacted_channel_event(client):
    response = client.post(
        "/channels/events",
        headers=CHANNEL_BRIDGE_HEADERS,
        json={
            "event_id": "evt-discord-001",
            "channel_type": "discord",
            "channel_config_id": "discord_home",
            "channel_id_hash": "sha256:" + "a" * 64,
            "author_id_hash": "sha256:" + "b" * 64,
            "message_id": "12345",
            "request_preview": "please help; token = secret-value",
            "route": "general_chat",
            "required_capability": "chat",
            "forwarded_to_yggdrasil": False,
            "status": "replied",
            "reply_preview": "hello; password: hunter2",
            "metadata": {"authorization": "Bearer secret", "history_count": 2},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "evt-discord-001"
    assert body["actor_role"] == "channel_bridge"
    assert body["status"] == "replied"
    assert body["forwarded_to_yggdrasil"] is False
    assert body["detail"]["request_preview"] == "please help; [REDACTED]"
    assert body["detail"]["reply_preview"] == "hello; [REDACTED]"
    assert body["detail"]["metadata"]["authorization"] == "[REDACTED]"


def test_channel_events_are_admin_read_only(client):
    create = client.post(
        "/channels/events",
        headers=CHANNEL_BRIDGE_HEADERS,
        json={
            "event_id": "evt-discord-002",
            "channel_type": "discord",
            "channel_config_id": "discord_home",
            "channel_id_hash": "sha256:" + "c" * 64,
            "author_id_hash": "sha256:" + "d" * 64,
            "message_id": "67890",
            "status": "forwarded",
            "route": "yggdrasil_canonical_action",
            "required_capability": "run_l1",
            "forwarded_to_yggdrasil": True,
        },
    )
    assert create.status_code == 201

    tool_list = client.get("/channels/events", headers=TOOL_HEADERS)
    assert tool_list.status_code == 403

    admin_list = client.get("/channels/events?status=forwarded", headers=ADMIN_HEADERS)
    assert admin_list.status_code == 200
    events = admin_list.json()
    assert len(events) == 1
    assert events[0]["id"] == "evt-discord-002"
    assert events[0]["required_capability"] == "run_l1"
    assert events[0]["forwarded_to_yggdrasil"] is True

    detail = client.get("/channels/events/evt-discord-002", headers=ADMIN_HEADERS)
    assert detail.status_code == 200
    assert detail.json()["detail"]["channel_id_hash"].startswith("sha256:")


def test_tool_key_cannot_create_channel_event_or_approve(client):
    response = client.post(
        "/channels/events",
        headers=TOOL_HEADERS,
        json={
            "channel_type": "discord",
            "status": "blocked",
            "blocked_reason": "unauthorized_user",
        },
    )
    assert response.status_code == 403

    approval_attempt = client.post(
        "/approvals/not-real/approve",
        headers=CHANNEL_BRIDGE_HEADERS,
        json={"nonce": "not-real"},
    )
    assert approval_attempt.status_code == 403
