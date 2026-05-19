from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import TaskChangeProposalModel, TaskModel
from app.routers.tasks import task_to_dict
from app.schemas import ApprovalDecision, TaskChangeProposalCreate, TaskChangeProposalReject
from app.services.task_change_service import (
    TaskChangeProposalError,
    apply_task_change_proposal,
    approve_task_change_proposal,
    create_task_change_proposal,
    proposal_to_dict,
    reject_task_change_proposal,
)

router = APIRouter(tags=["task-change-proposals"])


@router.post("/tasks/{task_id}/propose-change", status_code=status.HTTP_201_CREATED)
def propose_task_change(
    task_id: str,
    payload: TaskChangeProposalCreate,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    try:
        proposal, nonce = create_task_change_proposal(session, task, payload, actor_role=role)
    except TaskChangeProposalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit_event(
        session,
        role,
        "task_change.propose",
        "task_change_proposal",
        proposal.id,
        {"task_id": task.id, "approval_level": proposal.approval_level, "risk": proposal.risk},
    )
    session.commit()
    return proposal_to_dict(proposal, include_configs=True, nonce=nonce)


@router.get("/task-change-proposals")
def list_task_change_proposals(
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
    task_id: str | None = Query(default=None, min_length=1, max_length=128),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    query = session.query(TaskChangeProposalModel)
    if task_id:
        query = query.filter(TaskChangeProposalModel.task_id == task_id)
    if status_filter:
        query = query.filter(TaskChangeProposalModel.status == status_filter)
    proposals = query.order_by(TaskChangeProposalModel.created_at.desc()).limit(limit).all()
    return [proposal_to_dict(proposal) for proposal in proposals]


@router.get("/task-change-proposals/{proposal_id}")
def get_task_change_proposal(
    proposal_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(TaskChangeProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task change proposal not found")
    return proposal_to_dict(proposal, include_configs=True)


@router.post("/task-change-proposals/{proposal_id}/approve")
def approve_task_change(
    proposal_id: str,
    payload: ApprovalDecision,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    try:
        approve_task_change_proposal(proposal, payload.nonce)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TaskChangeProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(session, role, "task_change.approve", "task_change_proposal", proposal.id, {"task_id": proposal.task_id})
    session.commit()
    return proposal_to_dict(proposal, include_configs=True)


@router.post("/task-change-proposals/{proposal_id}/reject")
def reject_task_change(
    proposal_id: str,
    payload: TaskChangeProposalReject | None = None,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    try:
        reject_task_change_proposal(proposal)
    except TaskChangeProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    detail = {"task_id": proposal.task_id}
    if payload and payload.reason:
        detail["reason"] = payload.reason
    audit_event(session, role, "task_change.reject", "task_change_proposal", proposal.id, detail)
    session.commit()
    return proposal_to_dict(proposal, include_configs=True)


@router.post("/task-change-proposals/{proposal_id}/apply")
def apply_task_change(
    proposal_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    try:
        task = apply_task_change_proposal(session, proposal, actor_role=role)
    except TaskChangeProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(session, role, "task_change.apply", "task_change_proposal", proposal.id, {"task_id": proposal.task_id})
    session.commit()
    return {"proposal": proposal_to_dict(proposal, include_configs=True), "task": task_to_dict(task)}


def _get_proposal_or_404(session: Session, proposal_id: str) -> TaskChangeProposalModel:
    proposal = session.get(TaskChangeProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task change proposal not found")
    return proposal
