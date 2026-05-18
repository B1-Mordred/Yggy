from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import CapabilityProposalModel
from app.schemas import CapabilityProposalClose, CapabilityProposalCreate
from app.services.capability_proposal_service import (
    CapabilityProposalError,
    capability_proposal_to_dict,
    close_capability_proposal,
    create_capability_proposal,
)

router = APIRouter(prefix="/capability-proposals", tags=["capability-proposals"])


@router.post("/draft", status_code=status.HTTP_201_CREATED)
def draft_capability_proposal(
    payload: CapabilityProposalCreate,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    try:
        proposal = create_capability_proposal(session, payload)
    except CapabilityProposalError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    audit_event(
        session,
        role,
        "capability.propose",
        "capability_proposal",
        proposal.id,
        {
            "suggested_capability_id": proposal.suggested_capability_id,
            "suggested_task_type": proposal.suggested_task_type,
            "source_channel": proposal.source_channel,
        },
    )
    session.commit()
    return capability_proposal_to_dict(proposal)


@router.get("")
def list_capability_proposals(
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    requested_by: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    query = session.query(CapabilityProposalModel)
    if status_filter:
        query = query.filter(CapabilityProposalModel.status == status_filter)
    if requested_by:
        query = query.filter(CapabilityProposalModel.requested_by == requested_by)
    proposals = query.order_by(CapabilityProposalModel.created_at.desc()).limit(limit).all()
    return [capability_proposal_to_dict(proposal) for proposal in proposals]


@router.get("/{proposal_id}")
def get_capability_proposal(
    proposal_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    return capability_proposal_to_dict(proposal)


@router.post("/{proposal_id}/close")
def close_capability(
    proposal_id: str,
    payload: CapabilityProposalClose,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    try:
        close_capability_proposal(proposal, status=payload.status, reason=payload.reason)
    except CapabilityProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(
        session,
        role,
        f"capability.{payload.status}",
        "capability_proposal",
        proposal.id,
        {"suggested_capability_id": proposal.suggested_capability_id, "reason": payload.reason},
    )
    session.commit()
    return capability_proposal_to_dict(proposal)


@router.post("/{proposal_id}/accept")
def accept_capability(
    proposal_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    return close_capability(
        proposal_id,
        CapabilityProposalClose(status="accepted", reason="Accepted for implementation review."),
        role,
        session,
    )


@router.post("/{proposal_id}/reject")
def reject_capability(
    proposal_id: str,
    payload: CapabilityProposalClose | None = None,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    reason = payload.reason if payload else "Rejected from capability proposal review."
    return close_capability(
        proposal_id,
        CapabilityProposalClose(status="rejected", reason=reason),
        role,
        session,
    )


def _get_proposal_or_404(session: Session, proposal_id: str) -> CapabilityProposalModel:
    proposal = session.get(CapabilityProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability proposal not found")
    return proposal
