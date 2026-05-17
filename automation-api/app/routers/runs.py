from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import RunModel, TaskModel, utcnow
from app.schemas import RunUpdate
from app.services.run_state_service import CLAIMABLE_RUN_STATUSES, claim_log
from app.services.validation_service import redact_secrets

router = APIRouter(prefix="/runs", tags=["runs"])


def run_to_dict(run: RunModel) -> dict:
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status,
        "log": redact_secrets(run.log),
        "created_at": run.created_at,
        "completed_at": run.completed_at,
    }


@router.get("")
def list_runs(
    task_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> list[dict]:
    query = session.query(RunModel)
    if task_id:
        query = query.filter(RunModel.task_id == task_id)
    if status_filter:
        query = query.filter(RunModel.status == status_filter)
    runs = query.order_by(RunModel.created_at.desc()).limit(limit).all()
    return [run_to_dict(run) for run in runs]


@router.get("/{run_id}")
def get_run(
    run_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> dict:
    run = session.get(RunModel, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run_to_dict(run)


@router.post("/{run_id}/claim")
def claim_run(
    run_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> dict:
    run = session.query(RunModel).filter(RunModel.id == run_id).with_for_update().one_or_none()
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    if run.status not in CLAIMABLE_RUN_STATUSES or run.completed_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run is not claimable")

    dry_run = run.status == "queued_dry_run"
    run.status = "running_dry_run" if dry_run else "running"
    task = session.get(TaskModel, run.task_id)
    run.log = claim_log(task, run, dry_run=dry_run)
    audit_event(session, role, "run.claim", "run", run.id, {"task_id": run.task_id, "dry_run": dry_run})
    session.commit()
    payload = run_to_dict(run)
    payload["dry_run"] = dry_run
    payload["lease"] = run.log.get("lease") if isinstance(run.log, dict) else None
    return payload


@router.patch("/{run_id}")
def update_run(
    run_id: str,
    payload: RunUpdate,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> dict:
    run = session.get(RunModel, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    run.status = payload.status
    run.log = redact_secrets(payload.log)
    if payload.completed:
        run.completed_at = utcnow()
    audit_event(session, role, "run.update", "run", run.id, {"task_id": run.task_id, "status": run.status})
    session.commit()
    return run_to_dict(run)
