from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import ApiRole, require_roles
from app.database import get_session
from app.services.capability_gap_service import (
    capability_gap_to_dict,
    list_capability_gaps,
    match_capability_gap,
)

router = APIRouter(prefix="/capability-gaps", tags=["capability-gaps"])


class CapabilityGapMatchRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.get("")
def get_capability_gaps(
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    gaps = [capability_gap_to_dict(gap) for gap in list_capability_gaps(session)]
    return {
        "version": 1,
        "count": len(gaps),
        "gaps": gaps,
        "authority": "runtime_db_seeded_from_configs/capability_gaps.yaml",
    }


@router.post("/match")
def match_capability_gap_request(
    payload: CapabilityGapMatchRequest,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    gaps = [capability_gap_to_dict(gap) for gap in list_capability_gaps(session)]
    match = match_capability_gap(payload.text, gaps)
    return {
        "matched": match is not None,
        "gap": match,
    }
