from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import ApiRole, require_roles
from app.config import get_settings
from app.schemas import NotificationRequest
from app.services.discord_service import DiscordService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post("/discord/test")
def test_discord(
    payload: NotificationRequest,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
) -> dict:
    dry_run = get_settings().discord_dry_run if payload.dry_run is None else payload.dry_run
    if not dry_run and role != ApiRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required for non-dry-run Discord test")
    return DiscordService().send(payload.target, payload.content, dry_run=dry_run)


@router.post("/discord/send")
def send_discord(
    payload: NotificationRequest,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN, ApiRole.WORKER)),
) -> dict:
    return DiscordService().send(payload.target, payload.content, dry_run=payload.dry_run)
