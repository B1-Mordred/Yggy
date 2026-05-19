from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import SourceProposalModel
from app.schemas import ApprovalDecision, SourceProposalCreate, SourceProposalReject
from app.services.source_proposal_service import (
    SourceProposalError,
    apply_source_proposal,
    approve_source_proposal,
    create_source_proposal,
    reject_source_proposal,
    source_proposal_to_dict,
)

router = APIRouter(tags=["source-proposals"])


@router.post("/sources/propose", status_code=status.HTTP_201_CREATED)
def propose_source(
    payload: SourceProposalCreate,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    try:
        proposal, nonce = create_source_proposal(session, payload)
    except SourceProposalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit_event(session, role, "source.propose", "source_proposal", proposal.id, {"source_id": proposal.source_id})
    session.commit()
    return source_proposal_to_dict(proposal, nonce=nonce if role == ApiRole.ADMIN else None)


@router.get("/source-proposals")
def list_source_proposals(
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    query = session.query(SourceProposalModel)
    if status_filter:
        query = query.filter(SourceProposalModel.status == status_filter)
    proposals = query.order_by(SourceProposalModel.created_at.desc()).limit(limit).all()
    return [source_proposal_to_dict(proposal) for proposal in proposals]


@router.get("/source-proposals/{proposal_id}")
def get_source_proposal(
    proposal_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    return source_proposal_to_dict(proposal)


@router.post("/source-proposals/{proposal_id}/approve")
def approve_source(
    proposal_id: str,
    payload: ApprovalDecision,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    try:
        approve_source_proposal(proposal, payload.nonce)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except SourceProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(session, role, "source.approve", "source_proposal", proposal.id, {"source_id": proposal.source_id})
    session.commit()
    return source_proposal_to_dict(proposal)


@router.post("/source-proposals/{proposal_id}/reject")
def reject_source(
    proposal_id: str,
    payload: SourceProposalReject | None = None,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    try:
        reject_source_proposal(proposal)
    except SourceProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    detail = {"source_id": proposal.source_id}
    if payload and payload.reason:
        detail["reason"] = payload.reason
    audit_event(session, role, "source.reject", "source_proposal", proposal.id, detail)
    session.commit()
    return source_proposal_to_dict(proposal)


@router.post("/source-proposals/{proposal_id}/apply")
def apply_source(
    proposal_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    proposal = _get_proposal_or_404(session, proposal_id)
    try:
        apply_result = apply_source_proposal(proposal)
    except SourceProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(session, role, "source.apply", "source_proposal", proposal.id, {"source_id": proposal.source_id})
    session.commit()
    return {"proposal": source_proposal_to_dict(proposal), "apply": apply_result}


def _get_proposal_or_404(session: Session, proposal_id: str) -> SourceProposalModel:
    proposal = session.get(SourceProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source proposal not found")
    return proposal
