from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import RunModel, TaskModel
from app.policy import PolicyViolation, load_policy, validate_task_policy
from app.schemas import ApprovalLevel, TaskConfig, approval_at_least
from app.services.approval_service import create_approval_request, needs_initial_approval
from app.services.validation_service import redact_secrets

router = APIRouter(prefix="/tasks", tags=["tasks"])


def task_to_dict(task: TaskModel) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "type": task.type,
        "enabled": task.enabled,
        "owner": task.owner,
        "created_by": task.created_by,
        "approval_level": task.approval_level,
        "status": task.status,
        "config": redact_secrets(task.config),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def approval_to_public(approval, nonce: str | None = None) -> dict:
    payload = {
        "id": approval.id,
        "task_id": approval.task_id,
        "approval_level": approval.approval_level,
        "requested_by": approval.requested_by,
        "status": approval.status,
        "summary": approval.summary,
        "risk": approval.risk,
        "created_at": approval.created_at,
    }
    if nonce:
        payload["nonce"] = nonce
    return payload


@router.get("")
def list_tasks(
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> list[dict]:
    return [task_to_dict(task) for task in session.query(TaskModel).order_by(TaskModel.id).all()]


@router.get("/{task_id}")
def get_task(
    task_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return task_to_dict(task)


@router.post("/draft", status_code=status.HTTP_201_CREATED)
def create_draft_task(
    payload: TaskConfig,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    task_config = payload.model_copy(update={"enabled": False})
    _validate_or_422(task_config)
    if session.get(TaskModel, task_config.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="task already exists")

    task = TaskModel(
        id=task_config.id,
        name=task_config.name,
        type=task_config.type,
        enabled=False,
        owner=task_config.owner,
        created_by=task_config.created_by,
        approval_level=task_config.policy.approval_level.value,
        status="draft",
        config=task_config.model_dump(mode="json"),
    )
    session.add(task)
    session.flush()
    approval_payload = None
    if needs_initial_approval(task_config.policy.approval_level):
        approval, nonce = create_approval_request(session, task, requested_by=task_config.created_by)
        session.flush()
        approval_payload = approval_to_public(approval, nonce=nonce)
        task.status = "pending_approval"
    audit_event(session, role, "task.draft", "task", task.id, {"approval_level": task.approval_level})
    session.commit()
    return {"task": task_to_dict(task), "approval": approval_payload}


@router.put("/{task_id}")
def update_task(
    task_id: str,
    payload: TaskConfig,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    if payload.id != task_id:
        raise HTTPException(status_code=422, detail="payload id must match task id")

    _validate_or_422(payload)
    new_level = payload.policy.approval_level
    old_level = ApprovalLevel(task.approval_level)
    if role == ApiRole.TOOL:
        allowed = (
            not task.enabled
            and not payload.enabled
            and task.created_by == payload.created_by
            and not approval_at_least(old_level, ApprovalLevel.L2_LOCAL_WRITE)
            and not approval_at_least(new_level, ApprovalLevel.L2_LOCAL_WRITE)
        )
        if not allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required for this task update")

    task.name = payload.name
    task.type = payload.type
    task.enabled = payload.enabled if role == ApiRole.ADMIN else False
    task.owner = payload.owner
    task.created_by = payload.created_by
    task.approval_level = payload.policy.approval_level.value
    task.config = payload.model_dump(mode="json")
    task.status = "draft" if not task.enabled else "enabled"
    audit_event(session, role, "task.update", "task", task.id, {"approval_level": task.approval_level})
    session.commit()
    return task_to_dict(task)


@router.post("/{task_id}/request-approval", status_code=status.HTTP_201_CREATED)
def request_approval(
    task_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    approval, nonce = create_approval_request(session, task, requested_by=task.created_by)
    session.flush()
    task.status = "pending_approval"
    audit_event(session, role, "approval.request", "task", task.id, {"approval_id": approval.id})
    session.commit()
    return approval_to_public(approval, nonce=nonce)


@router.post("/{task_id}/pause")
def pause_task(
    task_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    level = ApprovalLevel(task.approval_level)
    if role == ApiRole.TOOL and approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required to pause L2+ task")
    task.enabled = False
    task.status = "paused"
    audit_event(session, role, "task.pause", "task", task.id)
    session.commit()
    return task_to_dict(task)


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_task(
    task_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    level = ApprovalLevel(task.approval_level)
    dry_run = bool(task.config.get("runtime", {}).get("dry_run", True))
    if role == ApiRole.TOOL:
        allowed = dry_run or (task.enabled and not approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE))
        if not allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="task cannot be run by tool role")
    if role == ApiRole.WORKER and not task.enabled and not dry_run:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="worker cannot run disabled non-dry-run task")

    run = RunModel(
        id=str(uuid.uuid4()),
        task_id=task.id,
        status="queued_dry_run" if dry_run else "queued",
        log=redact_secrets({"message": "run queued", "dry_run": dry_run, "task_id": task.id}),
    )
    session.add(run)
    audit_event(session, role, "task.run", "task", task.id, {"run_id": run.id, "dry_run": dry_run})
    session.commit()
    return {"run_id": run.id, "status": run.status}


def _validate_or_422(task_config: TaskConfig) -> None:
    try:
        validate_task_policy(task_config, load_policy())
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
