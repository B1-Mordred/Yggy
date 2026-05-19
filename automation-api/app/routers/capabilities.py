from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import ApiRole, require_roles
from app.schemas import CanonicalIntent
from app.services.capability_gateway import CapabilityError, get_capability, load_capability_registry, validate_intent

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("")
def list_capabilities(
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
) -> list[dict]:
    return [capability.summary() for capability in load_capability_registry().capabilities]


@router.get("/{capability_id}")
def get_capability_detail(
    capability_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
) -> dict:
    try:
        return get_capability(capability_id).summary()
    except CapabilityError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/validate-intent")
def validate_canonical_intent(
    payload: CanonicalIntent,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
) -> dict:
    return validate_intent(payload).model_dump(mode="json")


@router.post("/prepare-yggdrasil-request")
def prepare_yggdrasil_request(
    payload: CanonicalIntent,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
) -> dict:
    return validate_intent(payload, prepare=True).model_dump(mode="json")
