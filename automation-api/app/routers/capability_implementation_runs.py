from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import CapabilityProposalModel
from app.schemas import CapabilityImplementationRunCreate, CapabilityImplementationRunUpdate
from app.services.capability_implementation_service import (
    CapabilityImplementationError,
    create_implementation_run,
    get_implementation_run,
    implementation_run_to_dict,
    list_implementation_runs,
    update_implementation_run,
)

router = APIRouter(
    prefix="/capability-implementation-runs",
    tags=["capability-implementation-runs"],
    include_in_schema=False,
)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_capability_implementation_run(
    payload: CapabilityImplementationRunCreate,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(CapabilityProposalModel, payload.proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability proposal not found")
    try:
        run = create_implementation_run(
            session,
            proposal,
            created_by=payload.created_by,
            reason=payload.reason,
        )
    except CapabilityImplementationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(
        session,
        role,
        "capability_implementation.queued",
        "capability_implementation_run",
        run.id,
        {
            "proposal_id": run.proposal_id,
            "capability_id": run.capability_id,
            "branch": run.branch,
            "created_by": run.created_by,
        },
    )
    session.commit()
    return implementation_run_to_dict(run)


@router.get("")
def list_capability_implementation_runs(
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
    proposal_id: str | None = Query(default=None, min_length=1, max_length=64),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    return [
        implementation_run_to_dict(run)
        for run in list_implementation_runs(session, proposal_id=proposal_id, status=status_filter, limit=limit)
    ]


@router.get("/{run_id}")
def get_capability_implementation_run(
    run_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    run = get_implementation_run(session, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability implementation run not found")
    return implementation_run_to_dict(run)


@router.patch("/{run_id}")
def update_capability_implementation_run(
    run_id: str,
    payload: CapabilityImplementationRunUpdate,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    run = get_implementation_run(session, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability implementation run not found")
    before = run.status
    try:
        update_implementation_run(run, payload)
    except CapabilityImplementationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(
        session,
        role,
        f"capability_implementation.{run.status}",
        "capability_implementation_run",
        run.id,
        {
            "proposal_id": run.proposal_id,
            "capability_id": run.capability_id,
            "previous_status": before,
            "branch": run.branch,
            "commit_sha": run.commit_sha,
        },
    )
    session.commit()
    return implementation_run_to_dict(run)
