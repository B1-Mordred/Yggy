from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, Integer, JSON, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

ALLOWED_MEMORY_CATEGORIES = {
    "preference",
    "alias",
    "routine",
    "service_alias",
    "notification_style",
    "project_interest",
    "default",
    "note",
}
PROHIBITED_MEMORY_CATEGORIES = {
    "credential",
    "secret",
    "authorization",
    "approval",
    "payment",
    "medical",
    "legal",
}
MEMORY_STATUSES = {"pending", "active", "forgotten", "rejected"}
SCOPE_VALUES = {"user", "channel", "global"}
SLUG_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
SECRET_MARKERS = (
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "webhook_url",
    "private_key",
    "cookie",
    "nonce",
    "credential",
    "authorization",
)


class MemoryValidationError(ValueError):
    pass


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BragiMemoryRecord(Base):
    __tablename__ = "bragi_memory_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), default="non_secret", nullable=False)
    source: Mapped[str] = mapped_column(String(128), default="explicit_user_instruction", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BragiMemoryEvent(Base):
    __tablename__ = "bragi_memory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


_engine = None
_session_local: sessionmaker[Session] | None = None
_initialized = False


def memory_database_url() -> str:
    return (
        os.getenv("BRAGI_MEMORY_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or "sqlite+pysqlite:////tmp/bragi_memory.db"
    )


def get_engine():
    global _engine, _session_local
    if _engine is None:
        url = memory_database_url()
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url.endswith(":memory:"):
                kwargs["poolclass"] = StaticPool
        _engine = create_engine(url, **kwargs)
        _session_local = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return _engine


def reset_memory_store_for_tests(database_url: str = "sqlite+pysqlite:///:memory:") -> None:
    global _engine, _session_local, _initialized
    if _engine is not None:
        _engine.dispose()
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(database_url, **kwargs)
    _session_local = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    _initialized = False
    init_memory_store()


def init_memory_store() -> None:
    global _initialized
    if _initialized:
        return
    Base.metadata.create_all(bind=get_engine())
    _initialized = True


def session_scope() -> Session:
    init_memory_store()
    assert _session_local is not None
    return _session_local()


def memory_store_status() -> dict[str, Any]:
    try:
        init_memory_store()
        return {"configured": True, "connected": True, "backend": memory_database_url().split(":", 1)[0]}
    except Exception as exc:
        return {"configured": bool(memory_database_url()), "connected": False, "error": exc.__class__.__name__}


def safe_identifier(value: str, *, field_name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value).strip().lower()).strip("_.-")
    if not cleaned:
        raise MemoryValidationError(f"{field_name} is required")
    if not re.match(r"^[a-z0-9]", cleaned):
        cleaned = f"u_{cleaned}"
    cleaned = cleaned[:128]
    if not SLUG_RE.match(cleaned):
        raise MemoryValidationError(f"{field_name} must be slug-like")
    return cleaned


def contains_secret_like_material(value: Any) -> bool:
    text = json.dumps(value, default=str).lower() if not isinstance(value, str) else value.lower()
    if any(marker in text for marker in SECRET_MARKERS):
        return True
    if re.search(r"https://discord(?:app)?\.com/api/webhooks/\S+", text):
        return True
    if re.search(r"\b(?:sk|xoxb|ghp|github_pat)_[a-z0-9_\-]{16,}\b", text):
        return True
    return False


def validate_memory_payload(*, user_id: str, scope: str, category: str, key: str, value: Any) -> tuple[str, str, str, str]:
    clean_user_id = safe_identifier(user_id, field_name="user_id")
    clean_scope = str(scope or "user").strip().lower()
    if clean_scope not in SCOPE_VALUES:
        raise MemoryValidationError("memory scope is not allowed")
    clean_category = safe_identifier(category, field_name="category")
    if clean_category in PROHIBITED_MEMORY_CATEGORIES:
        raise MemoryValidationError("memory category is prohibited")
    if clean_category not in ALLOWED_MEMORY_CATEGORIES:
        raise MemoryValidationError("memory category is not allowed")
    clean_key = safe_identifier(key, field_name="key")
    if contains_secret_like_material({"user_id": clean_user_id, "category": clean_category, "key": clean_key, "value": value}):
        raise MemoryValidationError("memory contains secret-like material")
    return clean_user_id, clean_scope, clean_category, clean_key


def record_to_dict(record: BragiMemoryRecord, *, include_value: bool = True) -> dict[str, Any]:
    payload = {
        "id": record.id,
        "user_id": record.user_id,
        "scope": record.scope,
        "category": record.category,
        "key": record.key,
        "sensitivity": record.sensitivity,
        "source": record.source,
        "confidence": record.confidence,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expires_at": record.expires_at,
    }
    if include_value:
        payload["value"] = record.value_json
    return payload


def event(session: Session, *, record_id: str | None, user_id: str, action: str, detail: dict[str, Any] | None = None) -> None:
    session.add(
        BragiMemoryEvent(
            record_id=record_id,
            user_id=user_id,
            action=action,
            detail=detail or {},
        )
    )


def propose_memory(
    *,
    user_id: str,
    category: str,
    key: str,
    value: Any,
    scope: str = "user",
    source: str = "explicit_user_instruction",
    confidence: float = 1.0,
) -> dict[str, Any]:
    clean_user_id, clean_scope, clean_category, clean_key = validate_memory_payload(
        user_id=user_id,
        scope=scope,
        category=category,
        key=key,
        value=value,
    )
    clean_source = safe_identifier(source, field_name="source")
    confidence = max(0.0, min(float(confidence), 1.0))
    with session_scope() as session:
        record = BragiMemoryRecord(
            id=f"mem_{uuid.uuid4().hex}",
            user_id=clean_user_id,
            scope=clean_scope,
            category=clean_category,
            key=clean_key,
            value_json=value,
            sensitivity="non_secret",
            source=clean_source,
            confidence=confidence,
            status="pending",
        )
        session.add(record)
        event(
            session,
            record_id=record.id,
            user_id=clean_user_id,
            action="memory.propose",
            detail={"category": clean_category, "key": clean_key, "scope": clean_scope},
        )
        session.commit()
        return record_to_dict(record)


def commit_memory(*, memory_id: str, user_id: str) -> dict[str, Any]:
    clean_user_id = safe_identifier(user_id, field_name="user_id")
    with session_scope() as session:
        record = session.get(BragiMemoryRecord, memory_id)
        if not record or record.user_id != clean_user_id:
            raise MemoryValidationError("memory proposal not found")
        if record.status == "forgotten":
            raise MemoryValidationError("memory proposal was forgotten")
        record.status = "active"
        record.updated_at = utcnow()
        event(
            session,
            record_id=record.id,
            user_id=clean_user_id,
            action="memory.commit",
            detail={"category": record.category, "key": record.key},
        )
        session.commit()
        return record_to_dict(record)


def query_memory(
    *,
    user_id: str,
    category: str | None = None,
    include_pending: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clean_user_id = safe_identifier(user_id, field_name="user_id")
    clean_category = safe_identifier(category, field_name="category") if category else None
    statuses = ["active", "pending"] if include_pending else ["active"]
    with session_scope() as session:
        query = (
            session.query(BragiMemoryRecord)
            .filter(BragiMemoryRecord.user_id == clean_user_id)
            .filter(BragiMemoryRecord.status.in_(statuses))
        )
        if clean_category:
            query = query.filter(BragiMemoryRecord.category == clean_category)
        records = query.order_by(BragiMemoryRecord.updated_at.desc(), BragiMemoryRecord.id.asc()).limit(limit).all()
        return [record_to_dict(record) for record in records]


def forget_memory(
    *,
    user_id: str,
    memory_id: str | None = None,
    category: str | None = None,
    key: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    clean_user_id = safe_identifier(user_id, field_name="user_id")
    clean_category = safe_identifier(category, field_name="category") if category else None
    clean_key = safe_identifier(key, field_name="key") if key else None
    search_text = str(search or "").strip().lower()
    normalized_search = re.sub(r"[^a-z0-9]+", "_", search_text).strip("_")
    with session_scope() as session:
        query = (
            session.query(BragiMemoryRecord)
            .filter(BragiMemoryRecord.user_id == clean_user_id)
            .filter(BragiMemoryRecord.status.in_(["active", "pending"]))
        )
        if memory_id:
            query = query.filter(BragiMemoryRecord.id == memory_id)
        if clean_category:
            query = query.filter(BragiMemoryRecord.category == clean_category)
        if clean_key:
            query = query.filter(BragiMemoryRecord.key == clean_key)
        records = query.order_by(BragiMemoryRecord.updated_at.desc()).limit(limit).all()
        if search_text and not memory_id and not clean_category and not clean_key:
            records = [
                record
                for record in records
                if search_text in record.key.lower()
                or (normalized_search and normalized_search in record.key.lower())
                or search_text in record.category.lower()
                or search_text in json.dumps(record.value_json, default=str).lower()
            ]
        for record in records:
            record.status = "forgotten"
            record.updated_at = utcnow()
            event(
                session,
                record_id=record.id,
                user_id=clean_user_id,
                action="memory.forget",
                detail={"category": record.category, "key": record.key},
            )
        session.commit()
        return {"forgotten": len(records), "records": [record_to_dict(record) for record in records]}
