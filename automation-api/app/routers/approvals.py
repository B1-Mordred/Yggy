from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import ApprovalModel, TaskModel
from app.schemas import ApprovalDecision, ApprovalLevel
from app.services.approval_service import approve_request, reject_request, verify_nonce

router = APIRouter(prefix="/approvals", tags=["approvals"])


def approval_to_dict(approval: ApprovalModel) -> dict:
    return {
        "id": approval.id,
        "task_id": approval.task_id,
        "approval_level": approval.approval_level,
        "requested_by": approval.requested_by,
        "status": approval.status,
        "summary": approval.summary,
        "risk": approval.risk,
        "created_at": approval.created_at,
        "decided_at": approval.decided_at,
    }


@router.get("")
def list_approvals(
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> list[dict]:
    return [approval_to_dict(item) for item in session.query(ApprovalModel).order_by(ApprovalModel.created_at.desc()).all()]


@router.post("/{approval_id}/approve")
def approve(
    approval_id: str,
    payload: ApprovalDecision,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    approval = session.get(ApprovalModel, approval_id)
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="approval is not pending")
    if not verify_nonce(approval, payload.nonce):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid nonce")
    if ApprovalLevel(approval.approval_level) == ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="L4 approvals are manual only")

    approve_request(approval)
    task = session.get(TaskModel, approval.task_id)
    if task:
        task.enabled = True
        task.status = "enabled"
        task.config = {**task.config, "enabled": True}
    audit_event(session, role, "approval.approve", "approval", approval.id, {"task_id": approval.task_id})
    session.commit()
    return approval_to_dict(approval)


@router.post("/{approval_id}/reject")
def reject(
    approval_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    approval = session.get(ApprovalModel, approval_id)
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="approval is not pending")
    reject_request(approval)
    audit_event(session, role, "approval.reject", "approval", approval.id, {"task_id": approval.task_id})
    session.commit()
    return approval_to_dict(approval)
