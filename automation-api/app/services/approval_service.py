from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy.orm import Session

from app.models import ApprovalModel, TaskModel, utcnow
from app.schemas import ApprovalLevel


def hash_nonce(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def create_approval_request(session: Session, task: TaskModel, requested_by: str) -> tuple[ApprovalModel, str]:
    nonce = secrets.token_urlsafe(18)
    approval = ApprovalModel(
        id=str(uuid.uuid4()),
        task_id=task.id,
        approval_level=task.approval_level,
        requested_by=requested_by,
        status="pending",
        summary=f"Approval requested for task {task.id}: {task.name}",
        risk=task.approval_level,
        nonce_hash=hash_nonce(nonce),
    )
    session.add(approval)
    return approval, nonce


def verify_nonce(approval: ApprovalModel, nonce: str) -> bool:
    return secrets.compare_digest(approval.nonce_hash, hash_nonce(nonce))


def approve_request(approval: ApprovalModel) -> None:
    approval.status = "approved"
    approval.decided_at = utcnow()


def reject_request(approval: ApprovalModel) -> None:
    approval.status = "rejected"
    approval.decided_at = utcnow()


def needs_initial_approval(level: ApprovalLevel) -> bool:
    return level != ApprovalLevel.L0_READ_ONLY
