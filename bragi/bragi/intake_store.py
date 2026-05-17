from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .memory_store import Base, MemoryValidationError, contains_secret_like_material, get_engine, safe_identifier, session_scope


INTAKE_STATUSES = {
    "collecting",
    "awaiting_confirmation",
    "confirmed",
    "forwarded_to_yggdrasil",
    "cancelled",
    "expired",
    "failed",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BragiIntakeRecord(Base):
    __tablename__ = "bragi_intake_records"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(64), default="chat", index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="awaiting_confirmation", index=True, nullable=False)
    capability_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    intent_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(128), default="bragi_conversational_intake", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BragiIntakeEvent(Base):
    __tablename__ = "bragi_intake_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intake_id: Mapped[str | None] = mapped_column(String(96), index=True, nullable=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


_initialized = False


def reset_intake_store_for_tests(database_url: str = "sqlite+pysqlite:///:memory:") -> None:
    from .memory_store import reset_memory_store_for_tests

    global _initialized
    _initialized = False
    reset_memory_store_for_tests(database_url)
    init_intake_store()


def init_intake_store() -> None:
    global _initialized
    if _initialized:
        return
    engine = get_engine()
    BragiIntakeRecord.__table__.create(bind=engine, checkfirst=True)
    BragiIntakeEvent.__table__.create(bind=engine, checkfirst=True)
    _initialized = True


def intake_store_status() -> dict[str, Any]:
    try:
        init_intake_store()
        return {"configured": True, "connected": True}
    except Exception as exc:
        return {"configured": True, "connected": False, "error": exc.__class__.__name__}


def make_intake_id(now: datetime | None = None) -> str:
    stamp = (now or utcnow()).strftime("%Y%m%d_%H%M%S")
    return f"bragi_intake_{stamp}_{uuid.uuid4().hex[:8]}"


def validate_intake_payload(*, user_id: str, channel: str, status: str, intent: dict[str, Any], summary: dict[str, Any]) -> tuple[str, str, str]:
    clean_user_id = safe_identifier(user_id, field_name="user_id")
    clean_channel = safe_identifier(channel or "chat", field_name="channel")
    clean_status = str(status or "").strip().lower()
    if clean_status not in INTAKE_STATUSES:
        raise MemoryValidationError("intake status is not allowed")
    if not isinstance(intent, dict) or intent.get("intent") not in {"draft_task", "propose_task_change"}:
        raise MemoryValidationError("intake intent must be a canonical automation intent")
    if not intent.get("capability_id"):
        raise MemoryValidationError("intake intent requires capability_id")
    if contains_secret_like_material({"intent": intent, "summary": summary}):
        raise MemoryValidationError("intake contains secret-like material")
    return clean_user_id, clean_channel, clean_status


def event(session: Session, *, intake_id: str | None, user_id: str, action: str, detail: dict[str, Any] | None = None) -> None:
    session.add(
        BragiIntakeEvent(
            intake_id=intake_id,
            user_id=user_id,
            action=action,
            detail=detail or {},
        )
    )


def create_intake(
    *,
    user_id: str,
    channel: str = "chat",
    status: str = "awaiting_confirmation",
    intent: dict[str, Any],
    summary: dict[str, Any] | None = None,
    source: str = "bragi_conversational_intake",
    ttl_seconds: int = 86400,
) -> dict[str, Any]:
    clean_user_id, clean_channel, clean_status = validate_intake_payload(
        user_id=user_id,
        channel=channel,
        status=status,
        intent=intent,
        summary=summary or {},
    )
    clean_source = safe_identifier(source, field_name="source")
    now = utcnow()
    expires_at = now + timedelta(seconds=max(60, min(int(ttl_seconds), 604800)))
    with session_scope() as session:
        record = BragiIntakeRecord(
            id=make_intake_id(now),
            user_id=clean_user_id,
            channel=clean_channel,
            status=clean_status,
            capability_id=str(intent.get("capability_id")),
            intent_json=json.loads(json.dumps(intent, default=str)),
            summary_json=json.loads(json.dumps(summary or {}, default=str)),
            source=clean_source,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        session.add(record)
        event(
            session,
            intake_id=record.id,
            user_id=clean_user_id,
            action="intake.create",
            detail={"status": clean_status, "capability_id": record.capability_id},
        )
        session.commit()
        return record_to_dict(record)


def get_intake(*, intake_id: str, user_id: str) -> dict[str, Any]:
    clean_user_id = safe_identifier(user_id, field_name="user_id")
    clean_intake_id = safe_intake_id(intake_id)
    with session_scope() as session:
        record = session.get(BragiIntakeRecord, clean_intake_id)
        if not record or record.user_id != clean_user_id:
            raise MemoryValidationError("intake not found")
        maybe_expire_record(session, record)
        session.commit()
        return record_to_dict(record)


def list_intakes(*, user_id: str, include_inactive: bool = False, limit: int = 20) -> list[dict[str, Any]]:
    clean_user_id = safe_identifier(user_id, field_name="user_id")
    statuses = list(INTAKE_STATUSES) if include_inactive else ["collecting", "awaiting_confirmation"]
    with session_scope() as session:
        records = (
            session.query(BragiIntakeRecord)
            .filter(BragiIntakeRecord.user_id == clean_user_id)
            .filter(BragiIntakeRecord.status.in_(statuses))
            .order_by(BragiIntakeRecord.updated_at.desc(), BragiIntakeRecord.id.asc())
            .limit(max(1, min(int(limit), 50)))
            .all()
        )
        for record in records:
            maybe_expire_record(session, record)
        session.commit()
        return [record_to_dict(record) for record in records if include_inactive or record.status in {"collecting", "awaiting_confirmation"}]


def cancel_intake(*, intake_id: str, user_id: str) -> dict[str, Any]:
    return update_intake_status(intake_id=intake_id, user_id=user_id, status="cancelled", action="intake.cancel")


def mark_intake_confirmed(*, intake_id: str, user_id: str) -> dict[str, Any]:
    return update_intake_status(intake_id=intake_id, user_id=user_id, status="confirmed", action="intake.confirm")


def mark_intake_forwarded(*, intake_id: str, user_id: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return update_intake_status(
        intake_id=intake_id,
        user_id=user_id,
        status="forwarded_to_yggdrasil",
        action="intake.forward",
        detail=detail,
    )


def mark_intake_failed(*, intake_id: str, user_id: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return update_intake_status(intake_id=intake_id, user_id=user_id, status="failed", action="intake.fail", detail=detail)


def update_intake_status(
    *,
    intake_id: str,
    user_id: str,
    status: str,
    action: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_user_id = safe_identifier(user_id, field_name="user_id")
    clean_intake_id = safe_intake_id(intake_id)
    if status not in INTAKE_STATUSES:
        raise MemoryValidationError("intake status is not allowed")
    with session_scope() as session:
        record = session.get(BragiIntakeRecord, clean_intake_id)
        if not record or record.user_id != clean_user_id:
            raise MemoryValidationError("intake not found")
        maybe_expire_record(session, record)
        if record.status in {"expired", "cancelled"} and status not in {"expired"}:
            raise MemoryValidationError(f"intake is {record.status}")
        record.status = status
        record.updated_at = utcnow()
        event(session, intake_id=record.id, user_id=clean_user_id, action=action, detail=detail)
        session.commit()
        return record_to_dict(record)


def maybe_expire_record(session: Session, record: BragiIntakeRecord) -> None:
    if record.status in {"collecting", "awaiting_confirmation"} and as_aware(record.expires_at) <= utcnow():
        record.status = "expired"
        record.updated_at = utcnow()
        event(session, intake_id=record.id, user_id=record.user_id, action="intake.expire", detail={})


def safe_intake_id(value: str) -> str:
    text = str(value or "").strip()
    if not re.match(r"^bragi_intake_[a-z0-9_]{8,64}$", text):
        raise MemoryValidationError("intake_id is invalid")
    return text


def as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def record_to_dict(record: BragiIntakeRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "channel": record.channel,
        "status": record.status,
        "capability_id": record.capability_id,
        "intent": record.intent_json,
        "summary": record.summary_json,
        "source": record.source,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expires_at": record.expires_at,
    }
