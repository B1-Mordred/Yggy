from __future__ import annotations

from sqlalchemy.orm import Session

from .auth import ApiRole
from .models import AuditEventModel


def audit_event(
    session: Session,
    actor_role: ApiRole | str,
    action: str,
    resource_type: str,
    resource_id: str,
    detail: dict | None = None,
) -> None:
    session.add(
        AuditEventModel(
            actor_role=actor_role.value if isinstance(actor_role, ApiRole) else str(actor_role),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail or {},
        )
    )
