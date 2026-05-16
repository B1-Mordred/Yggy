from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import RunModel
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
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> list[dict]:
    return [run_to_dict(run) for run in session.query(RunModel).order_by(RunModel.created_at.desc()).all()]


@router.get("/{run_id}")
def get_run(
    run_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    run = session.get(RunModel, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run_to_dict(run)
