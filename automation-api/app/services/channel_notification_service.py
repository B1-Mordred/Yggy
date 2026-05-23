from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    CapabilityImplementationRunModel,
    CapabilityProposalModel,
    ChannelNotificationModel,
    utcnow,
)
from app.schemas import ChannelNotificationMark
from app.services.validation_service import redact_secrets


CHANNEL_NOTIFICATION_PENDING_STATUSES = {"pending"}
CHANNEL_NOTIFICATION_FINAL_STATUSES = {"sent", "failed", "skipped"}
CHANNEL_NOTIFICATION_CHANNELS = {"discord", "discord_dm", "openwebui"}
CHANNEL_NOTIFICATION_MESSAGE_LIMIT = 3000
CHANNEL_NOTIFICATION_TEXT_LIMIT = 800
CHANNEL_NOTIFICATION_DEPTH_LIMIT = 4
CHANNEL_NOTIFICATION_KEYS_LIMIT = 25
CHANNEL_NOTIFICATION_ITEMS_LIMIT = 25


class ChannelNotificationError(ValueError):
    pass


def enqueue_channel_notification(
    session: Session,
    *,
    kind: str,
    channel: str,
    user_id: str,
    resource_type: str,
    resource_id: str,
    dedupe_key: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ChannelNotificationModel:
    safe_dedupe_key = _safe_identifier(dedupe_key, default=str(uuid.uuid4()), limit=255)
    existing = (
        session.query(ChannelNotificationModel)
        .filter(ChannelNotificationModel.dedupe_key == safe_dedupe_key)
        .one_or_none()
    )
    if existing is not None:
        return existing

    notification = ChannelNotificationModel(
        id=str(uuid.uuid4()),
        kind=_clean_slug(kind, default="notification"),
        channel=normalize_notification_channel(channel),
        user_id=_safe_identifier(user_id, default="local_user", limit=128),
        status="pending",
        resource_type=_clean_slug(resource_type, default="resource"),
        resource_id=_safe_identifier(resource_id, default="unknown", limit=128),
        dedupe_key=safe_dedupe_key,
        message=_safe_message(message),
        notification_metadata=_bounded_value(metadata or {}),
        attempts=0,
        error="",
    )
    session.add(notification)
    session.flush()
    return notification


def enqueue_implementation_status_notification(
    session: Session,
    *,
    run: CapabilityImplementationRunModel,
    proposal: CapabilityProposalModel,
    previous_status: str | None = None,
) -> ChannelNotificationModel:
    status = _safe_identifier(run.status, default="unknown", limit=32)
    message = render_implementation_status_message(run=run, proposal=proposal, previous_status=previous_status)
    return enqueue_channel_notification(
        session,
        kind="capability_implementation_status",
        channel=proposal.source_channel,
        user_id=proposal.requested_by,
        resource_type="capability_implementation_run",
        resource_id=run.id,
        dedupe_key=f"capability_implementation_status:{run.id}:{status}",
        message=message,
        metadata={
            "proposal_id": proposal.id,
            "proposal_title": proposal.title,
            "capability_id": run.capability_id,
            "run_id": run.id,
            "status": status,
            "previous_status": previous_status,
            "branch": run.branch,
            "commit_sha": run.commit_sha,
            "source_channel": proposal.source_channel,
            "requested_by": proposal.requested_by,
        },
    )


def render_implementation_status_message(
    *,
    run: CapabilityImplementationRunModel,
    proposal: CapabilityProposalModel,
    previous_status: str | None = None,
) -> str:
    title = _safe_message(proposal.title, limit=120)
    capability_id = _safe_message(run.capability_id, limit=128)
    run_id = _safe_message(run.id, limit=64)
    branch = _safe_message(run.branch, limit=160)
    short_commit = _safe_message((run.commit_sha or "")[:12], limit=12)
    status = _safe_message(run.status, limit=32)
    previous = _safe_message(previous_status or "", limit=32)
    summary = _safe_message(run.summary, limit=600)
    error = _safe_message(run.error, limit=600)

    header = f"Bragi here. Implementation status changed to `{status}` for `{title}`."
    if status == "queued":
        body = (
            "I put it on the implementation queue. The workbench is marked, the lockbox stays shut, "
            "and no automation has been enabled."
        )
    elif status == "running":
        body = (
            "The local implementation runner picked it up. The forge is hot, but the blast radius is still caged."
        )
    elif status == "completed":
        commit_line = f"\nCommit: `{short_commit}`" if short_commit else ""
        body = (
            "Implementation completed and produced a local code change. No task was enabled, no admin approval was issued, "
            "and nothing was deployed by this notification."
            f"{commit_line}"
        )
    elif status == "failed":
        body = "Implementation failed. Irritating, but better on the bench than bleeding into production."
        if error:
            body = f"{body}\nReason: {error}"
    else:
        body = "The run changed state. I am reporting it before anyone has to go spelunking through logs."

    parts = [
        header,
        "",
        body,
        "",
        f"Capability: `{capability_id}`",
        f"Run: `{run_id}`",
    ]
    if branch:
        parts.append(f"Branch: `{branch}`")
    if previous and previous != status:
        parts.append(f"Previous status: `{previous}`")
    if summary and status in {"queued", "running", "completed"}:
        parts.extend(["", f"Summary: {summary}"])
    parts.extend(
        [
            "",
            "This is a status report only. Yggy approval, deployment, and execution boundaries remain unchanged.",
        ]
    )
    return _safe_message("\n".join(parts), limit=CHANNEL_NOTIFICATION_MESSAGE_LIMIT)


def list_pending_channel_notifications(
    session: Session,
    *,
    channel: str,
    user_id: str | None = None,
    limit: int = 20,
) -> list[ChannelNotificationModel]:
    query = (
        session.query(ChannelNotificationModel)
        .filter(ChannelNotificationModel.channel == normalize_notification_channel(channel))
        .filter(ChannelNotificationModel.status.in_(CHANNEL_NOTIFICATION_PENDING_STATUSES))
    )
    if user_id:
        query = query.filter(ChannelNotificationModel.user_id == _safe_identifier(user_id, default="local_user", limit=128))
    return query.order_by(ChannelNotificationModel.created_at.asc(), ChannelNotificationModel.id.asc()).limit(limit).all()


def get_channel_notification(session: Session, notification_id: str) -> ChannelNotificationModel | None:
    return session.get(ChannelNotificationModel, notification_id)


def mark_channel_notification(
    notification: ChannelNotificationModel,
    payload: ChannelNotificationMark,
) -> ChannelNotificationModel:
    if payload.status not in CHANNEL_NOTIFICATION_FINAL_STATUSES:
        raise ChannelNotificationError("channel notification status is not final")
    notification.status = payload.status
    notification.attempts += 1
    notification.error = _safe_message(payload.error, limit=1000)
    notification.updated_at = utcnow()
    if payload.status in {"sent", "skipped"}:
        notification.sent_at = utcnow()
    return notification


def channel_notification_to_dict(notification: ChannelNotificationModel) -> dict[str, Any]:
    return redact_secrets(
        {
            "id": notification.id,
            "kind": notification.kind,
            "channel": notification.channel,
            "user_id": notification.user_id,
            "status": notification.status,
            "resource_type": notification.resource_type,
            "resource_id": notification.resource_id,
            "message": notification.message,
            "metadata": notification.notification_metadata,
            "attempts": notification.attempts,
            "error": notification.error,
            "created_at": notification.created_at,
            "updated_at": notification.updated_at,
            "sent_at": notification.sent_at,
        }
    )


def normalize_notification_channel(channel: str | None) -> str:
    text = (channel or "").strip().lower()
    if text in CHANNEL_NOTIFICATION_CHANNELS:
        return text
    if text.startswith("discord_dm"):
        return "discord_dm"
    if text.startswith("discord"):
        return "discord"
    if text.startswith("openwebui") or text.startswith("open_webui"):
        return "openwebui"
    return "openwebui"


def _safe_message(value: Any, *, limit: int = CHANNEL_NOTIFICATION_MESSAGE_LIMIT) -> str:
    text = str(redact_secrets(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _safe_identifier(value: Any, *, default: str, limit: int) -> str:
    text = " ".join(_safe_message(value, limit=limit).split())
    return (text or default)[:limit]


def _clean_slug(value: Any, *, default: str) -> str:
    text = str(redact_secrets(value or "")).strip().lower().replace("-", "_")
    cleaned = "".join(char for char in text if char.isalnum() or char == "_").strip("_")
    return (cleaned or default)[:64]


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    value = redact_secrets(value)
    if depth >= CHANNEL_NOTIFICATION_DEPTH_LIMIT:
        if isinstance(value, (dict, list)):
            return "[TRUNCATED]"
        return _bounded_scalar(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= CHANNEL_NOTIFICATION_KEYS_LIMIT:
                result["truncated_keys"] = True
                break
            result[str(redact_secrets(key))[:80]] = _bounded_value(child, depth=depth + 1)
        return result
    if isinstance(value, list):
        result = [_bounded_value(item, depth=depth + 1) for item in value[:CHANNEL_NOTIFICATION_ITEMS_LIMIT]]
        if len(value) > CHANNEL_NOTIFICATION_ITEMS_LIMIT:
            result.append("[TRUNCATED]")
        return result
    return _bounded_scalar(value)


def _bounded_scalar(value: Any) -> Any:
    value = redact_secrets(value)
    if isinstance(value, str):
        return _safe_message(value, limit=CHANNEL_NOTIFICATION_TEXT_LIMIT)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_message(str(value), limit=CHANNEL_NOTIFICATION_TEXT_LIMIT)
