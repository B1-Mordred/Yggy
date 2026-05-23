from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import AuditEventModel
from app.schemas import ChannelEventCreate, ChannelEventStatus, ChannelNotificationMark
from app.services.channel_notification_service import (
    channel_notification_to_dict,
    get_channel_notification,
    list_pending_channel_notifications,
    mark_channel_notification,
)
from app.services.validation_service import redact_secrets

router = APIRouter(prefix="/channels", tags=["channels"])

CHANNEL_EVENT_RESOURCE_TYPE = "channel_event"
CHANNEL_EVENT_PREVIEW_LIMIT = 240
CHANNEL_EVENT_METADATA_TEXT_LIMIT = 500
CHANNEL_EVENT_METADATA_KEYS = 20
CHANNEL_EVENT_METADATA_ITEMS = 20
CHANNEL_EVENT_METADATA_DEPTH = 3


@router.post("/events", status_code=status.HTTP_201_CREATED)
def create_channel_event(
    payload: ChannelEventCreate,
    role: ApiRole = Depends(require_roles(ApiRole.CHANNEL_BRIDGE, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    event_id = payload.event_id or str(uuid4())
    detail = _channel_event_detail(payload)
    event = AuditEventModel(
        actor_role=role.value,
        action=f"channel.{payload.channel_type}.{payload.status.value}",
        resource_type=CHANNEL_EVENT_RESOURCE_TYPE,
        resource_id=event_id,
        detail=detail,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return channel_event_to_dict(event)


@router.get("/events")
def list_channel_events(
    channel_type: Literal["discord", "openwebui", "api"] | None = None,
    status_filter: ChannelEventStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = session.query(AuditEventModel).filter(AuditEventModel.resource_type == CHANNEL_EVENT_RESOURCE_TYPE)
    if channel_type and status_filter:
        query = query.filter(AuditEventModel.action == f"channel.{channel_type}.{status_filter.value}")
    elif channel_type:
        query = query.filter(AuditEventModel.action.like(f"channel.{channel_type}.%"))
    elif status_filter:
        query = query.filter(AuditEventModel.action.like(f"channel.%.{status_filter.value}"))

    events = query.order_by(AuditEventModel.created_at.desc(), AuditEventModel.id.desc()).limit(limit).all()
    return [channel_event_to_dict(event) for event in events]


@router.get("/events/{event_id}")
def get_channel_event(
    event_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    event = (
        session.query(AuditEventModel)
        .filter(AuditEventModel.resource_type == CHANNEL_EVENT_RESOURCE_TYPE)
        .filter(AuditEventModel.resource_id == event_id)
        .order_by(AuditEventModel.created_at.desc(), AuditEventModel.id.desc())
        .first()
    )
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel event not found")
    return channel_event_to_dict(event)


@router.get("/notifications/pending", include_in_schema=False)
def pending_channel_notifications(
    channel: Literal["discord", "discord_dm", "openwebui"],
    user_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=20, ge=1, le=50),
    role: ApiRole = Depends(require_roles(ApiRole.CHANNEL_BRIDGE, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    notifications = list_pending_channel_notifications(session, channel=channel, user_id=user_id, limit=limit)
    return {
        "notifications": [channel_notification_to_dict(notification) for notification in notifications],
        "count": len(notifications),
    }


@router.post("/notifications/{notification_id}/mark", include_in_schema=False)
def mark_channel_notification_delivery(
    notification_id: str,
    payload: ChannelNotificationMark,
    role: ApiRole = Depends(require_roles(ApiRole.CHANNEL_BRIDGE, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    notification = get_channel_notification(session, notification_id)
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel notification not found")
    if notification.status == "pending":
        mark_channel_notification(notification, payload)
        session.commit()
        session.refresh(notification)
    return channel_notification_to_dict(notification)


def channel_event_to_dict(event: AuditEventModel) -> dict[str, Any]:
    detail = _bounded_value(redact_secrets(event.detail if isinstance(event.detail, dict) else {}))
    return {
        "id": event.resource_id,
        "audit_id": event.id,
        "actor_role": event.actor_role,
        "action": event.action,
        "channel_type": detail.get("channel_type"),
        "channel_config_id": detail.get("channel_config_id"),
        "status": detail.get("status"),
        "route": detail.get("route"),
        "required_capability": detail.get("required_capability"),
        "forwarded_to_yggdrasil": bool(detail.get("forwarded_to_yggdrasil")),
        "blocked_reason": detail.get("blocked_reason"),
        "detail": detail,
        "created_at": event.created_at,
    }


def _channel_event_detail(payload: ChannelEventCreate) -> dict[str, Any]:
    return {
        "channel_type": payload.channel_type,
        "channel_config_id": _clean_identifier(payload.channel_config_id),
        "channel_id_hash": payload.channel_id_hash,
        "author_id_hash": payload.author_id_hash,
        "message_id": _clean_identifier(payload.message_id),
        "request_preview": _safe_preview(payload.request_preview),
        "route": _clean_identifier(payload.route),
        "required_capability": _clean_identifier(payload.required_capability),
        "forwarded_to_yggdrasil": payload.forwarded_to_yggdrasil,
        "status": payload.status.value,
        "blocked_reason": _clean_identifier(payload.blocked_reason),
        "reply_preview": _safe_preview(payload.reply_preview),
        "metadata": _bounded_value(payload.metadata),
    }


def _clean_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(redact_secrets(value)).split())
    if not text:
        return None
    return text[:128]


def _safe_preview(value: str | None, *, limit: int = CHANNEL_EVENT_PREVIEW_LIMIT) -> str | None:
    if value is None:
        return None
    text = " ".join(str(redact_secrets(value)).split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    value = redact_secrets(value)
    if depth >= CHANNEL_EVENT_METADATA_DEPTH:
        if isinstance(value, (dict, list)):
            return "[TRUNCATED]"
        return _bounded_scalar(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= CHANNEL_EVENT_METADATA_KEYS:
                result["truncated_keys"] = True
                break
            result[str(key)[:80]] = _bounded_value(child, depth=depth + 1)
        return result
    if isinstance(value, list):
        result = [_bounded_value(item, depth=depth + 1) for item in value[:CHANNEL_EVENT_METADATA_ITEMS]]
        if len(value) > CHANNEL_EVENT_METADATA_ITEMS:
            result.append("[TRUNCATED]")
        return result
    return _bounded_scalar(value)


def _bounded_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_preview(value, limit=CHANNEL_EVENT_METADATA_TEXT_LIMIT)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_preview(str(value), limit=CHANNEL_EVENT_METADATA_TEXT_LIMIT)
