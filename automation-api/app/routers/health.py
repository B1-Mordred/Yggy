from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.config import get_settings
from app.database import get_session
from app.models import HeartbeatModel, utcnow
from app.schemas import HeartbeatUpdate
from app.services.validation_service import redact_secrets

router = APIRouter(tags=["health"])

WORKER_HEARTBEAT_MAX_AGE_SECONDS = 180


def heartbeat_to_dict(heartbeat: HeartbeatModel | None) -> dict:
    if heartbeat is None:
        return {"ok": False, "status": "missing", "last_seen_at": None, "age_seconds": None}
    now = utcnow()
    last_seen = heartbeat.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    age_seconds = max(0, int((now - last_seen).total_seconds()))
    return {
        "ok": heartbeat.status == "ok" and age_seconds <= WORKER_HEARTBEAT_MAX_AGE_SECONDS,
        "status": heartbeat.status,
        "last_seen_at": heartbeat.last_seen_at,
        "age_seconds": age_seconds,
        "max_age_seconds": WORKER_HEARTBEAT_MAX_AGE_SECONDS,
        "detail": redact_secrets(heartbeat.detail),
    }


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    database = {"connected": False}
    try:
        session.execute(text("SELECT 1"))
        database["connected"] = True
    except Exception as exc:  # pragma: no cover - exercised only with unavailable DB
        database["error"] = exc.__class__.__name__
    worker = heartbeat_to_dict(session.get(HeartbeatModel, "automation-worker")) if database["connected"] else {"ok": False}
    ok = database["connected"] and worker.get("ok") is not False
    return {
        "status": "ok" if ok else "degraded",
        "version": get_settings().version,
        "database": database,
        "worker": worker,
    }


@router.post("/health/heartbeat")
def update_heartbeat(
    payload: HeartbeatUpdate,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> dict:
    heartbeat = session.get(HeartbeatModel, payload.service)
    if heartbeat is None:
        heartbeat = HeartbeatModel(service=payload.service, status=payload.status, detail=redact_secrets(payload.detail))
        session.add(heartbeat)
    else:
        heartbeat.status = payload.status
        heartbeat.detail = redact_secrets(payload.detail)
        heartbeat.last_seen_at = utcnow()
    audit_event(session, role, "heartbeat.update", "service", payload.service, {"status": payload.status})
    session.commit()
    return heartbeat_to_dict(heartbeat)
