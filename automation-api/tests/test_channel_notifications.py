from __future__ import annotations

from conftest import ADMIN_HEADERS, CHANNEL_BRIDGE_HEADERS, TOOL_HEADERS
from sqlalchemy.orm import Session

from app.database import get_engine
from app.services.channel_notification_service import enqueue_channel_notification


def test_channel_bridge_key_can_list_and_mark_pending_notification(client):
    with Session(get_engine()) as session:
        notification = enqueue_channel_notification(
            session,
            kind="capability_implementation_status",
            channel="discord",
            user_id="local_user",
            resource_type="capability_implementation_run",
            resource_id="run-1",
            dedupe_key="test:notification:run-1:queued",
            message="Bragi here. queued; token = secret-value",
            metadata={"authorization": "Bearer secret", "status": "queued"},
        )
        notification_id = notification.id
        session.commit()

    listed = client.get(
        "/channels/notifications/pending?channel=discord&user_id=local_user",
        headers=CHANNEL_BRIDGE_HEADERS,
    )
    marked = client.post(
        f"/channels/notifications/{notification_id}/mark",
        headers=CHANNEL_BRIDGE_HEADERS,
        json={"status": "sent"},
    )
    no_longer_pending = client.get(
        "/channels/notifications/pending?channel=discord&user_id=local_user",
        headers=CHANNEL_BRIDGE_HEADERS,
    )

    assert listed.status_code == 200
    body = listed.json()
    assert body["count"] == 1
    assert body["notifications"][0]["id"] == notification_id
    assert body["notifications"][0]["message"] == "Bragi here. queued; [REDACTED]"
    assert body["notifications"][0]["metadata"]["authorization"] == "[REDACTED]"
    assert marked.status_code == 200
    assert marked.json()["status"] == "sent"
    assert marked.json()["sent_at"] is not None
    assert no_longer_pending.json()["notifications"] == []


def test_tool_key_cannot_read_or_mark_channel_notifications(client):
    with Session(get_engine()) as session:
        notification = enqueue_channel_notification(
            session,
            kind="capability_implementation_status",
            channel="discord_dm",
            user_id="local_user",
            resource_type="capability_implementation_run",
            resource_id="run-2",
            dedupe_key="test:notification:run-2:queued",
            message="queued",
        )
        notification_id = notification.id
        session.commit()

    listed = client.get(
        "/channels/notifications/pending?channel=discord_dm&user_id=local_user",
        headers=TOOL_HEADERS,
    )
    marked = client.post(
        f"/channels/notifications/{notification_id}/mark",
        headers=TOOL_HEADERS,
        json={"status": "sent"},
    )

    assert listed.status_code == 403
    assert marked.status_code == 403


def test_admin_can_read_openwebui_pending_notifications(client):
    with Session(get_engine()) as session:
        enqueue_channel_notification(
            session,
            kind="capability_implementation_status",
            channel="openwebui",
            user_id="local_user",
            resource_type="capability_implementation_run",
            resource_id="run-3",
            dedupe_key="test:notification:run-3:queued",
            message="queued for Open WebUI",
        )
        session.commit()

    listed = client.get(
        "/channels/notifications/pending?channel=openwebui&user_id=local_user",
        headers=ADMIN_HEADERS,
    )

    assert listed.status_code == 200
    assert listed.json()["notifications"][0]["channel"] == "openwebui"
