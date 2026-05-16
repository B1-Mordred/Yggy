from __future__ import annotations

import secrets
from enum import Enum
from typing import Callable

from fastapi import Depends, Header, HTTPException, status

from .config import get_settings


class ApiRole(str, Enum):
    TOOL = "tool"
    ADMIN = "admin"
    WORKER = "worker"


def classify_api_key(api_key: str | None) -> ApiRole:
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing API key")

    settings = get_settings()
    if settings.admin_api_key and secrets.compare_digest(api_key, settings.admin_api_key):
        return ApiRole.ADMIN
    if settings.worker_api_key and secrets.compare_digest(api_key, settings.worker_api_key):
        return ApiRole.WORKER
    if settings.tool_api_key and secrets.compare_digest(api_key, settings.tool_api_key):
        return ApiRole.TOOL
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid API key")


def get_current_role(x_automation_api_key: str | None = Header(default=None)) -> ApiRole:
    return classify_api_key(x_automation_api_key)


def require_roles(*allowed: ApiRole) -> Callable[[ApiRole], ApiRole]:
    def dependency(role: ApiRole = Depends(get_current_role)) -> ApiRole:
        if role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return role

    return dependency
