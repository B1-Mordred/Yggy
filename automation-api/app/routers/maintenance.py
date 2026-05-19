from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import ApiRole, require_roles
from app.config import get_settings
from app.database import get_session
from app.schemas import RetentionRequest, StaleRunRecoveryRequest
from app.services.retention_service import RetentionPolicy, apply_retention
from app.services.run_state_service import recover_stale_runs

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.post("/retention")
def run_retention(
    payload: RetentionRequest | None = None,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> dict:
    request = payload or RetentionRequest()
    settings = get_settings()
    policy = RetentionPolicy(
        run_retention_days=request.run_retention_days or settings.run_retention_days,
        audit_retention_days=request.audit_retention_days or settings.audit_retention_days,
        temp_task_retention_hours=(
            settings.temp_task_retention_hours
            if request.temp_task_retention_hours is None
            else request.temp_task_retention_hours
        ),
    )
    return apply_retention(session, actor_role=role, policy=policy, dry_run=request.dry_run)


@router.post("/stale-runs")
def run_stale_run_recovery(
    payload: StaleRunRecoveryRequest | None = None,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> dict:
    request = payload or StaleRunRecoveryRequest()
    result = recover_stale_runs(
        session,
        actor_role=role,
        task_id=request.task_id,
        stale_after_seconds=request.stale_after_seconds,
        dry_run=request.dry_run,
        limit=request.limit,
    )
    session.commit()
    return result
