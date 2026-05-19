from __future__ import annotations

import uuid
from datetime import timedelta
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.config import get_settings
from app.database import get_session
from app.models import ApprovalModel, RunModel, TaskModel, utcnow
from app.policy import PolicyViolation, load_policy, validate_task_policy
from app.schemas import ApprovalLevel, TaskConfig, TaskRunRequest, approval_at_least
from app.services.approval_service import create_approval_request, needs_initial_approval, reject_request
from app.services.run_state_service import ACTIVE_RUN_STATUSES, COMPLETED_RUN_STATUSES, recover_stale_runs
from app.services.task_version_service import link_latest_task_config_version_to_approval, record_task_config_version
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
    include_archived: bool = Query(default=False),
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN, ApiRole.WORKER)),
    session: Session = Depends(get_session),
) -> list[dict]:
    query = session.query(TaskModel)
    if not include_archived:
        query = query.filter(TaskModel.status != "archived")
    return [task_to_dict(task) for task in query.order_by(TaskModel.id).all()]


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
    return create_draft_task_record(payload, role=role, session=session)


def create_draft_task_record(
    payload: TaskConfig,
    *,
    role: ApiRole,
    session: Session,
    audit_details: dict | None = None,
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
        record_task_config_version(
            session,
            task,
            actor_role=role,
            change_type="draft",
            approval_id=approval.id,
            summary="Draft task configuration awaiting initial approval.",
        )
        approval_payload = approval_to_public(approval, nonce=nonce)
        task.status = "pending_approval"
    else:
        record_task_config_version(
            session,
            task,
            actor_role=role,
            change_type="draft",
            summary="Draft task configuration created without initial approval requirement.",
        )
    audit_event(session, role, "task.draft", "task", task.id, {"approval_level": task.approval_level, **(audit_details or {})})
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

    old_level_text = task.approval_level
    task.name = payload.name
    task.type = payload.type
    task.enabled = payload.enabled if role == ApiRole.ADMIN else False
    task.owner = payload.owner
    task.created_by = payload.created_by
    task.approval_level = payload.policy.approval_level.value
    task.config = payload.model_dump(mode="json")
    task.status = "draft" if not task.enabled else "enabled"
    record_task_config_version(
        session,
        task,
        actor_role=role,
        change_type="update",
        summary=f"Task config updated; approval level {old_level_text} -> {task.approval_level}.",
    )
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
    linked_version = link_latest_task_config_version_to_approval(session, task, approval_id=approval.id)
    if not linked_version:
        record_task_config_version(
            session,
            task,
            actor_role=role,
            change_type="approval_request",
            approval_id=approval.id,
            summary="Approval requested for current task configuration.",
        )
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
    task.config = {**task.config, "enabled": False}
    record_task_config_version(
        session,
        task,
        actor_role=role,
        change_type="pause",
        summary="Task paused and enabled flag mirrored into task config.",
    )
    audit_event(session, role, "task.pause", "task", task.id)
    session.commit()
    return task_to_dict(task)


@router.post("/{task_id}/archive")
def archive_task(
    task_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    if task.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="enabled tasks must be paused before archive")
    if task.status == "archived":
        return task_to_dict(task)
    if task.status not in {"draft", "pending_approval", "rejected", "paused"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"task status {task.status} cannot be archived")

    pending_approvals = (
        session.query(ApprovalModel)
        .filter(ApprovalModel.task_id == task.id)
        .filter(ApprovalModel.status == "pending")
        .all()
    )
    for approval in pending_approvals:
        reject_request(approval)

    task.enabled = False
    task.status = "archived"
    task.config = {**task.config, "enabled": False}
    record_task_config_version(
        session,
        task,
        actor_role=role,
        change_type="archive",
        summary="Disabled task archived; audit history retained.",
    )
    audit_event(
        session,
        role,
        "task.archive",
        "task",
        task.id,
        {"rejected_pending_approvals": [approval.id for approval in pending_approvals]},
    )
    session.commit()
    return task_to_dict(task)


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_task(
    task_id: str,
    payload: TaskRunRequest | None = None,
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

    force = bool(payload.force) if payload else False
    if force and role != ApiRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required to force a duplicate run")

    return queue_task_run(session, task, dry_run=dry_run, force=force, actor_role=role)


def queue_task_run(
    session: Session,
    task: TaskModel,
    *,
    dry_run: bool,
    force: bool = False,
    actor_role: ApiRole | str,
) -> dict:
    task = _locked_task(session, task.id)
    recover_stale_runs(session, actor_role=actor_role, task_id=task.id, dry_run=False, limit=25)

    active_run = _latest_active_run(session, task.id)
    if active_run:
        audit_event(
            session,
            actor_role,
            "task.run.denied",
            "task",
            task.id,
            {"reason": "active_run_exists", "run_id": active_run.id, "status": active_run.status, "dry_run": dry_run},
        )
        session.commit()
        return {
            "run_id": active_run.id,
            "status": active_run.status,
            "queued": False,
            "deduplicated": True,
            "reason": "active_run_exists",
            "message": "run not queued because this task already has an active run",
        }

    rate_limit = _rate_limit_denial(session, task, dry_run=dry_run)
    if rate_limit:
        audit_event(
            session,
            actor_role,
            "task.run.denied",
            "task",
            task.id,
            {"reason": rate_limit["reason"], "dry_run": dry_run, **rate_limit},
        )
        session.commit()
        return {
            "run_id": None,
            "status": "rate_limited",
            "queued": False,
            "deduplicated": True,
            **rate_limit,
            "message": "run not queued because this task exceeded its configured run safety limits",
        }

    recent_run = _latest_recent_completed_live_run(session, task.id) if not dry_run and not force else None
    if recent_run:
        audit_event(
            session,
            actor_role,
            "task.run.denied",
            "task",
            task.id,
            {
                "reason": "recent_completed_run",
                "run_id": recent_run.id,
                "dedupe_seconds": get_settings().run_dedupe_seconds,
                "dry_run": dry_run,
            },
        )
        session.commit()
        return {
            "run_id": recent_run.id,
            "status": "duplicate_recent",
            "queued": False,
            "deduplicated": True,
            "reason": "recent_completed_run",
            "duplicate_of": recent_run.id,
            "dedupe_seconds": get_settings().run_dedupe_seconds,
            "message": "run not queued because this task completed within the dedupe window",
        }

    run = RunModel(
        id=str(uuid.uuid4()),
        task_id=task.id,
        status="queued_dry_run" if dry_run else "queued",
        log=redact_secrets({"message": "run queued", "dry_run": dry_run, "task_id": task.id}),
    )
    session.add(run)
    audit_event(session, actor_role, "task.run", "task", task.id, {"run_id": run.id, "dry_run": dry_run})
    session.commit()
    return {"run_id": run.id, "status": run.status, "queued": True, "deduplicated": False}


def _locked_task(session: Session, task_id: str) -> TaskModel:
    return session.query(TaskModel).filter(TaskModel.id == task_id).with_for_update().one()


def _latest_active_run(session: Session, task_id: str) -> RunModel | None:
    return (
        session.query(RunModel)
        .filter(RunModel.task_id == task_id)
        .filter(RunModel.status.in_(ACTIVE_RUN_STATUSES))
        .filter(RunModel.completed_at.is_(None))
        .order_by(RunModel.created_at.desc())
        .first()
    )


def _rate_limit_denial(session: Session, task: TaskModel, *, dry_run: bool) -> dict | None:
    policy = _effective_task_policy(task)
    now = utcnow()

    if policy.min_seconds_between_runs > 0:
        latest_run = _latest_accepted_run(session, task.id)
        if latest_run:
            elapsed = max(0, (now - _aware_datetime(latest_run.created_at)).total_seconds())
            if elapsed < policy.min_seconds_between_runs:
                retry_after = ceil(policy.min_seconds_between_runs - elapsed)
                return {
                    "reason": "min_seconds_between_runs",
                    "limit": policy.min_seconds_between_runs,
                    "retry_after_seconds": retry_after,
                    "latest_run_id": latest_run.id,
                    "latest_run_created_at": latest_run.created_at.isoformat() if latest_run.created_at else None,
                    "dry_run": dry_run,
                }

    max_runs_per_hour = policy.max_runs_per_hour
    if max_runs_per_hour is not None:
        hourly_cutoff = now - timedelta(hours=1)
        hourly_count = _run_count_since(session, task.id, hourly_cutoff)
        if hourly_count >= max_runs_per_hour:
            return {
                "reason": "max_runs_per_hour",
                "limit": max_runs_per_hour,
                "window_seconds": 3600,
                "current_count": hourly_count,
                "retry_after_seconds": _retry_after_for_window(session, task.id, hourly_cutoff, 3600, now),
                "dry_run": dry_run,
            }

    max_runs_per_day = policy.max_runs_per_day
    if max_runs_per_day is not None:
        daily_cutoff = now - timedelta(days=1)
        daily_count = _run_count_since(session, task.id, daily_cutoff)
        if daily_count >= max_runs_per_day:
            return {
                "reason": "max_runs_per_day",
                "limit": max_runs_per_day,
                "window_seconds": 86400,
                "current_count": daily_count,
                "retry_after_seconds": _retry_after_for_window(session, task.id, daily_cutoff, 86400, now),
                "dry_run": dry_run,
            }

    return None


def _effective_task_policy(task: TaskModel):
    return TaskConfig.model_validate(task.config).policy


def _latest_accepted_run(session: Session, task_id: str) -> RunModel | None:
    return (
        session.query(RunModel)
        .filter(RunModel.task_id == task_id)
        .order_by(RunModel.created_at.desc())
        .first()
    )


def _run_count_since(session: Session, task_id: str, cutoff) -> int:
    return (
        session.query(RunModel)
        .filter(RunModel.task_id == task_id)
        .filter(RunModel.created_at >= cutoff)
        .count()
    )


def _retry_after_for_window(session: Session, task_id: str, cutoff, window_seconds: int, now) -> int:
    oldest_counted = (
        session.query(RunModel)
        .filter(RunModel.task_id == task_id)
        .filter(RunModel.created_at >= cutoff)
        .order_by(RunModel.created_at.asc())
        .first()
    )
    if not oldest_counted:
        return 0
    retry_at = _aware_datetime(oldest_counted.created_at) + timedelta(seconds=window_seconds)
    return max(0, ceil((retry_at - now).total_seconds()))


def _aware_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=utcnow().tzinfo)
    return value


def _latest_recent_completed_live_run(session: Session, task_id: str) -> RunModel | None:
    dedupe_seconds = get_settings().run_dedupe_seconds
    if dedupe_seconds <= 0:
        return None
    cutoff = utcnow() - timedelta(seconds=dedupe_seconds)
    return (
        session.query(RunModel)
        .filter(RunModel.task_id == task_id)
        .filter(RunModel.status.in_(COMPLETED_RUN_STATUSES))
        .filter(RunModel.completed_at >= cutoff)
        .order_by(RunModel.completed_at.desc())
        .first()
    )


def _validate_or_422(task_config: TaskConfig) -> None:
    try:
        validate_task_policy(task_config, load_policy())
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
