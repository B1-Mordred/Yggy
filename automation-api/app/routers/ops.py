from __future__ import annotations

import copy
import secrets
from html import escape
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, classify_api_key
from app.config import get_settings
from app.database import get_session
from app.models import ApprovalModel, AuditEventModel, HeartbeatModel, RunModel, TaskModel, utcnow
from app.policy import PolicyViolation, load_policy, validate_task_policy
from app.routers.health import WORKER_HEARTBEAT_MAX_AGE_SECONDS, heartbeat_to_dict
from app.routers.tasks import queue_task_run
from app.schemas import ApprovalLevel, TaskConfig, approval_at_least
from app.services.approval_service import create_approval_request, approve_request, reject_request, verify_nonce
from app.services.task_version_service import (
    config_diff,
    record_task_config_version,
    task_config_version_by_number,
    task_config_version_for_approval,
    task_config_version_to_dict,
    task_config_versions,
)
from app.services.validation_service import redact_secrets

router = APIRouter(tags=["ops"])
basic_security = HTTPBasic(auto_error=False)
OPS_ACTION_HEADER = "approval-decision"
OPS_RUN_ACTION_HEADER = "manual-run"
OPS_TASK_STATE_ACTION_HEADER = "task-state"
OPS_VERSION_REVERT_ACTION_HEADER = "version-revert"
MAX_RUN_DETAIL_ITEMS = 10
MAX_RUN_DETAIL_ERRORS = 10
MAX_RUN_DETAIL_TEXT = 6000
MAX_RUN_DETAIL_FIELD_TEXT = 1200
MAX_RUN_DETAIL_DEPTH = 5
MAX_RUN_DETAIL_KEYS = 25
MAX_AUDIT_DETAIL_TEXT = 1200
MAX_AUDIT_DETAIL_DEPTH = 4
MAX_AUDIT_DETAIL_KEYS = 20
RUN_DETAIL_SECRET_KEY_MARKERS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "webhook",
)
RUN_DETAIL_NON_SECRET_KEYS = {"webhook_id"}


class OpsApprovalDecision(BaseModel):
    nonce: str = Field(min_length=8, max_length=256)


class OpsApprovalRejection(BaseModel):
    reason: str = Field(default="", max_length=500)


class OpsTaskRunRequest(BaseModel):
    mode: Literal["dry_run", "live"] = "dry_run"


class OpsTaskVersionRevertRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


def require_ops_access(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_security)] = None,
    x_automation_api_key: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    if not settings.ops_dashboard_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ops dashboard is disabled")
    if x_automation_api_key:
        try:
            if classify_api_key(x_automation_api_key) == ApiRole.ADMIN:
                return
        except HTTPException:
            pass
    if not settings.ops_dashboard_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ops dashboard password is not configured",
        )
    if (
        credentials
        and secrets.compare_digest(credentials.username, settings.ops_dashboard_user)
        and secrets.compare_digest(credentials.password, settings.ops_dashboard_password)
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="ops dashboard credentials required",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_ops_action_header(x_yggy_ops_action: str | None = Header(default=None, alias="X-Yggy-Ops-Action")) -> None:
    if x_yggy_ops_action != OPS_ACTION_HEADER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing ops action header")


def require_ops_run_action_header(
    x_yggy_ops_action: str | None = Header(default=None, alias="X-Yggy-Ops-Action"),
) -> None:
    if x_yggy_ops_action != OPS_RUN_ACTION_HEADER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing ops run action header")


def require_ops_task_state_action_header(
    x_yggy_ops_action: str | None = Header(default=None, alias="X-Yggy-Ops-Action"),
) -> None:
    if x_yggy_ops_action != OPS_TASK_STATE_ACTION_HEADER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing ops task state action header")


def require_ops_version_revert_action_header(
    x_yggy_ops_action: str | None = Header(default=None, alias="X-Yggy-Ops-Action"),
) -> None:
    if x_yggy_ops_action != OPS_VERSION_REVERT_ACTION_HEADER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing ops version revert action header")


@router.get("/ops", response_class=HTMLResponse, include_in_schema=False)
def ops_dashboard(_: None = Depends(require_ops_access)) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


@router.get("/ops/status", include_in_schema=False)
def ops_status(
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
) -> dict:
    now = utcnow()
    database = {"connected": False}
    try:
        session.execute(text("SELECT 1"))
        database["connected"] = True
    except Exception as exc:  # pragma: no cover - exercised only with unavailable DB
        database["error"] = exc.__class__.__name__

    tasks = session.query(TaskModel).order_by(TaskModel.id).all()
    recent_runs = session.query(RunModel).order_by(RunModel.created_at.desc()).limit(20).all()
    latest_by_task: dict[str, RunModel] = {}
    for run in recent_runs:
        latest_by_task.setdefault(run.task_id, run)

    pending_approvals = (
        session.query(ApprovalModel)
        .filter(ApprovalModel.status == "pending")
        .order_by(ApprovalModel.created_at.desc())
        .limit(20)
        .all()
    )
    active_runs = [run for run in recent_runs if run.status in {"queued", "queued_dry_run", "running", "running_dry_run"}]
    latest_retention = (
        session.query(AuditEventModel)
        .filter(AuditEventModel.action.in_(["maintenance.retention.preview", "maintenance.retention.apply"]))
        .order_by(AuditEventModel.created_at.desc())
        .first()
    )
    worker = heartbeat_to_dict(session.get(HeartbeatModel, "automation-worker")) if database["connected"] else {"ok": False}

    return {
        "generated_at": now,
        "service": {
            "status": "ok" if database["connected"] and worker.get("ok") is not False else "degraded",
            "database": database,
            "worker": worker,
        },
        "counts": {
            "tasks": len(tasks),
            "enabled_tasks": sum(1 for task in tasks if task.enabled),
            "pending_approvals": len(pending_approvals),
            "active_runs": len(active_runs),
        },
        "tasks": [_task_summary(task, latest_by_task.get(task.id)) for task in tasks],
        "recent_runs": [_run_summary(run) for run in recent_runs[:10]],
        "pending_approvals": [
            _approval_summary(approval, session.get(TaskModel, approval.task_id), session=session)
            for approval in pending_approvals
        ],
        "retention": {
            "policy": {
                "run_retention_days": get_settings().run_retention_days,
                "audit_retention_days": get_settings().audit_retention_days,
                "temp_task_retention_hours": get_settings().temp_task_retention_hours,
            },
            "latest": _audit_summary(latest_retention),
        },
        "safety": {
            "read_only": False,
            "approval_actions_enabled": True,
            "openapi_exposed": False,
            "worker_heartbeat_max_age_seconds": WORKER_HEARTBEAT_MAX_AGE_SECONDS,
        },
    }


@router.get("/ops/runs/{run_id}", include_in_schema=False)
def ops_run_detail(
    run_id: str,
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
) -> dict:
    run = session.get(RunModel, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    task = session.get(TaskModel, run.task_id)
    return _run_detail(run, task)


@router.get("/ops/tasks/{task_id}", include_in_schema=False)
def ops_task_detail(
    task_id: str,
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    recent_runs = (
        session.query(RunModel)
        .filter(RunModel.task_id == task.id)
        .order_by(RunModel.created_at.desc())
        .limit(10)
        .all()
    )
    approvals = (
        session.query(ApprovalModel)
        .filter(ApprovalModel.task_id == task.id)
        .order_by(ApprovalModel.created_at.desc())
        .limit(10)
        .all()
    )
    return _task_detail(
        session=session,
        task=task,
        latest_run=recent_runs[0] if recent_runs else None,
        recent_runs=recent_runs,
        approvals=approvals,
    )


@router.get("/ops/audit", include_in_schema=False)
def ops_audit_events(
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=100),
    actor_role: str | None = Query(default=None, min_length=1, max_length=64),
    action: str | None = Query(default=None, min_length=1, max_length=128),
    resource_type: str | None = Query(default=None, min_length=1, max_length=64),
    resource_id: str | None = Query(default=None, min_length=1, max_length=128),
    q: str | None = Query(default=None, min_length=1, max_length=128),
) -> dict:
    query = session.query(AuditEventModel)
    if actor_role:
        query = query.filter(AuditEventModel.actor_role == actor_role)
    if action:
        query = query.filter(AuditEventModel.action == action)
    if resource_type:
        query = query.filter(AuditEventModel.resource_type == resource_type)
    if resource_id:
        query = query.filter(AuditEventModel.resource_id.ilike(f"%{resource_id}%"))
    if q:
        query = query.filter(
            or_(
                AuditEventModel.actor_role.ilike(f"%{q}%"),
                AuditEventModel.action.ilike(f"%{q}%"),
                AuditEventModel.resource_type.ilike(f"%{q}%"),
                AuditEventModel.resource_id.ilike(f"%{q}%"),
            )
        )
    events = query.order_by(AuditEventModel.created_at.desc()).limit(limit).all()
    return {
        "generated_at": utcnow(),
        "limit": limit,
        "filters": {
            "actor_role": actor_role,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "q": q,
        },
        "events": [_audit_event_detail(event) for event in events],
    }


@router.post("/ops/tasks/{task_id}/run", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
def ops_run_task(
    task_id: str,
    payload: OpsTaskRunRequest,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_run_action_header),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    level = ApprovalLevel(task.approval_level)
    if level == ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="L4 tasks are manual only")

    dry_run = payload.mode == "dry_run"
    if not dry_run:
        if not task.enabled:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="task must be enabled for live run")
        if approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin API required for live L2+ task")

    result = queue_task_run(session, task, dry_run=dry_run, actor_role="ops_dashboard")
    return {
        **result,
        "mode": payload.mode,
        "task_id": task.id,
        "message": result.get("message") or f"{payload.mode.replace('_', '-')} run queued",
    }


@router.post("/ops/tasks/{task_id}/pause", include_in_schema=False)
def ops_pause_task(
    task_id: str,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_task_state_action_header),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    level = ApprovalLevel(task.approval_level)
    if approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin API required to pause L2+ task")
    task.enabled = False
    task.status = "paused"
    task.config = {**task.config, "enabled": False}
    record_task_config_version(
        session,
        task,
        actor_role="ops_dashboard",
        change_type="pause",
        summary="Task paused from ops dashboard and enabled flag mirrored into task config.",
    )
    audit_event(session, "ops_dashboard", "task.pause", "task", task.id, {"surface": "ops_ui"})
    session.commit()
    return _task_summary(task, None)


@router.post("/ops/tasks/{task_id}/resume", include_in_schema=False)
def ops_resume_task(
    task_id: str,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_task_state_action_header),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    level = ApprovalLevel(task.approval_level)
    if approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin API required to resume L2+ task")
    if task.status == "rejected":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="rejected task requires a new approval")
    if task.status == "pending_approval":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="task is still pending approval")
    if level == ApprovalLevel.L1_NOTIFY_ONLY and not _has_approved_task_approval(session, task):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="approved L1 task required to resume")

    task.enabled = True
    task.status = "enabled"
    task.config = {**task.config, "enabled": True}
    record_task_config_version(
        session,
        task,
        actor_role="ops_dashboard",
        change_type="resume",
        summary="Task resumed from ops dashboard and enabled flag mirrored into task config.",
    )
    audit_event(session, "ops_dashboard", "task.resume", "task", task.id, {"surface": "ops_ui"})
    session.commit()
    return _task_summary(task, None)


@router.post("/ops/tasks/{task_id}/versions/{version}/revert", include_in_schema=False)
def ops_revert_task_config_version(
    task_id: str,
    version: int,
    payload: OpsTaskVersionRevertRequest | None = None,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_version_revert_action_header),
    session: Session = Depends(get_session),
) -> dict:
    task = session.get(TaskModel, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    source_version = task_config_version_by_number(session, task.id, version)
    if not source_version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task config version not found")
    latest_version = task_config_versions(session, task.id, limit=1)
    if latest_version and source_version.version == latest_version[0].version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot revert to the current version")
    pending_approval = (
        session.query(ApprovalModel)
        .filter(ApprovalModel.task_id == task.id)
        .filter(ApprovalModel.status == "pending")
        .first()
    )
    if pending_approval:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="resolve pending approval before reverting config",
        )

    restored_config = copy.deepcopy(source_version.config if isinstance(source_version.config, dict) else {})
    restored_config["id"] = task.id
    restored_config["enabled"] = False
    task_config = _validated_revert_task_config(restored_config)

    old_version_number = latest_version[0].version if latest_version else None
    task.name = task_config.name
    task.type = task_config.type
    task.enabled = False
    task.owner = task_config.owner
    task.created_by = task_config.created_by
    task.approval_level = task_config.policy.approval_level.value
    task.status = "pending_approval"
    task.config = task_config.model_dump(mode="json")
    session.flush()

    approval, nonce = create_approval_request(session, task, requested_by="ops_dashboard")
    session.flush()
    new_version = record_task_config_version(
        session,
        task,
        actor_role="ops_dashboard",
        change_type="revert_draft",
        approval_id=approval.id,
        summary=f"Reverted draft from config version {source_version.version}; task remains disabled pending approval.",
    )
    audit_event(
        session,
        "ops_dashboard",
        "task.config.revert",
        "task",
        task.id,
        {
            "surface": "ops_ui",
            "source_version": source_version.version,
            "previous_latest_version": old_version_number,
            "new_version": new_version.version,
            "approval_id": approval.id,
            "reason": payload.reason if payload else "",
        },
    )
    session.commit()
    return {
        "task": _task_summary(task, None),
        "source_version": task_config_version_to_dict(session, source_version, include_config=False),
        "new_version": task_config_version_to_dict(session, new_version, include_config=False),
        "approval": _approval_summary(approval, task, session=session),
        "approval_nonce": nonce,
        "message": "revert draft created; task remains disabled until approval is accepted",
    }


@router.post("/ops/approvals/{approval_id}/approve", include_in_schema=False)
def ops_approve_approval(
    approval_id: str,
    payload: OpsApprovalDecision,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_action_header),
    session: Session = Depends(get_session),
) -> dict:
    approval = session.get(ApprovalModel, approval_id)
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="approval is not pending")
    if not verify_nonce(approval, payload.nonce):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid nonce")
    if ApprovalLevel(approval.approval_level) == ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="L4 approvals are manual only")

    approve_request(approval)
    task = session.get(TaskModel, approval.task_id)
    if task:
        task.enabled = True
        task.status = "enabled"
        task.config = {**task.config, "enabled": True}
        record_task_config_version(
            session,
            task,
            actor_role="ops_dashboard",
            change_type="approval_approve",
            approval_id=approval.id,
            summary="Approval accepted from ops dashboard and task enabled.",
        )
    audit_event(
        session,
        "ops_dashboard",
        "approval.approve",
        "approval",
        approval.id,
        {"task_id": approval.task_id, "surface": "ops_ui"},
    )
    session.commit()
    return _approval_summary(approval, task, session=session)


@router.post("/ops/approvals/{approval_id}/reject", include_in_schema=False)
def ops_reject_approval(
    approval_id: str,
    payload: OpsApprovalRejection | None = None,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_action_header),
    session: Session = Depends(get_session),
) -> dict:
    approval = session.get(ApprovalModel, approval_id)
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="approval is not pending")

    reject_request(approval)
    task = session.get(TaskModel, approval.task_id)
    if task:
        task.enabled = False
        task.status = "rejected"
        task.config = {**task.config, "enabled": False}
        record_task_config_version(
            session,
            task,
            actor_role="ops_dashboard",
            change_type="approval_reject",
            approval_id=approval.id,
            summary="Approval rejected from ops dashboard and task disabled.",
        )
    audit_event(
        session,
        "ops_dashboard",
        "approval.reject",
        "approval",
        approval.id,
        {"task_id": approval.task_id, "surface": "ops_ui", "reason": (payload.reason if payload else "")},
    )
    session.commit()
    return _approval_summary(approval, task, session=session)


def _task_summary(task: TaskModel, latest_run: RunModel | None) -> dict:
    config = task.config or {}
    trigger = config.get("trigger") if isinstance(config.get("trigger"), dict) else {}
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    return {
        "id": task.id,
        "name": task.name,
        "type": task.type,
        "enabled": task.enabled,
        "status": task.status,
        "approval_level": task.approval_level,
        "dry_run": bool(runtime.get("dry_run", True)),
        "trigger": {"kind": trigger.get("kind"), "cron": trigger.get("cron"), "timezone": trigger.get("timezone")},
        "output": {"channel": output.get("channel"), "target": output.get("target")},
        "latest_run": _run_summary(latest_run) if latest_run else None,
        "updated_at": task.updated_at,
    }


def _task_detail(
    *,
    session: Session,
    task: TaskModel,
    latest_run: RunModel | None,
    recent_runs: list[RunModel],
    approvals: list[ApprovalModel],
) -> dict:
    return {
        "task": _task_summary(task, latest_run),
        "config": _redacted_task_config(task),
        "approvals": [_approval_history_summary(approval) for approval in approvals],
        "recent_runs": [_run_summary(run) for run in recent_runs],
        "config_versions": [
            task_config_version_to_dict(session, version, include_config=False)
            for version in task_config_versions(session, task.id)
        ],
        "allowed_actions": _task_action_eligibility(session, task),
    }


def _redacted_task_config(task: TaskModel) -> Any:
    config = task.config if isinstance(task.config, dict) else {}
    return _bounded_value(
        redact_secrets(config),
        max_depth=MAX_RUN_DETAIL_DEPTH,
        max_keys=MAX_RUN_DETAIL_KEYS,
        text_limit=MAX_RUN_DETAIL_TEXT,
        field_text_limit=MAX_RUN_DETAIL_FIELD_TEXT,
    )


def _task_action_eligibility(session: Session, task: TaskModel) -> dict:
    level = ApprovalLevel(task.approval_level)
    dry_run = _allowed_action(True, "available")
    if level == ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE:
        dry_run = _allowed_action(False, "L4 tasks are manual only")

    if level == ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE:
        live_run = _allowed_action(False, "L4 tasks are manual only")
    elif not task.enabled:
        live_run = _allowed_action(False, "task must be enabled for live run")
    elif approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE):
        live_run = _allowed_action(False, "admin API required for live L2+ task")
    else:
        live_run = _allowed_action(True, "available")

    if approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE):
        pause = _allowed_action(False, "admin API required to pause L2+ task")
    elif not task.enabled:
        pause = _allowed_action(False, "task is already paused or disabled")
    else:
        pause = _allowed_action(True, "available")

    if approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE):
        resume = _allowed_action(False, "admin API required to resume L2+ task")
    elif task.enabled:
        resume = _allowed_action(False, "task is already enabled")
    elif task.status == "rejected":
        resume = _allowed_action(False, "rejected task requires a new approval")
    elif task.status == "pending_approval":
        resume = _allowed_action(False, "task is still pending approval")
    elif level == ApprovalLevel.L1_NOTIFY_ONLY and not _has_approved_task_approval(session, task):
        resume = _allowed_action(False, "approved L1 task required to resume")
    else:
        resume = _allowed_action(True, "available")

    return {"dry_run": dry_run, "live_run": live_run, "pause": pause, "resume": resume}


def _validated_revert_task_config(config: dict) -> TaskConfig:
    try:
        task_config = TaskConfig.model_validate(config)
        validate_task_policy(task_config, load_policy())
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors(include_context=False)) from exc
    except PolicyViolation as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors) from exc
    return task_config


def _allowed_action(allowed: bool, reason: str) -> dict:
    return {"allowed": allowed, "reason": reason}


def _has_approved_task_approval(session: Session, task: TaskModel) -> bool:
    return (
        session.query(ApprovalModel)
        .filter(ApprovalModel.task_id == task.id)
        .filter(ApprovalModel.approval_level == task.approval_level)
        .filter(ApprovalModel.status == "approved")
        .first()
        is not None
    )


def _run_summary(run: RunModel | None) -> dict | None:
    if run is None:
        return None
    log = run.log if isinstance(run.log, dict) else {}
    result = log.get("result") if isinstance(log.get("result"), dict) else {}
    notification = log.get("notification") if isinstance(log.get("notification"), dict) else {}
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "result_status": result.get("status"),
        "failed_count": result.get("failed_count"),
        "notify": result.get("notify"),
        "notification": {
            "sent": notification.get("sent") if notification else None,
            "dry_run": notification.get("dry_run") if notification else None,
            "target": notification.get("target") if notification else None,
            "transport": notification.get("transport") if notification else None,
        },
    }


def _run_detail(run: RunModel, task: TaskModel | None) -> dict:
    log = _as_dict(_bounded_value(redact_secrets(run.log if isinstance(run.log, dict) else {})))
    result = _as_dict(log.get("result"))
    notification = log.get("notification") if isinstance(log.get("notification"), dict) else None
    notification_decision = (
        log.get("notification_decision") if isinstance(log.get("notification_decision"), dict) else {}
    )
    return {
        "run": _run_summary(run),
        "task": _task_summary(task, run) if task else {"id": run.task_id},
        "digest": _digest_detail(result),
        "n8n": _n8n_detail(result),
        "notification_decision": notification_decision,
        "notification": notification,
        "failure": _failure_detail(log),
    }


def _digest_detail(result: dict) -> dict | None:
    if not result:
        return None
    items = _as_list(result.get("items"))
    errors = _as_list(result.get("errors"))
    has_digest_fields = any(
        key in result
        for key in ("title", "message", "items", "errors", "source_count", "summary_mode", "summary_error")
    )
    if not has_digest_fields:
        return None
    return {
        "status": result.get("status"),
        "title": _truncate_text(result.get("title"), MAX_RUN_DETAIL_FIELD_TEXT),
        "message": _truncate_text(result.get("message"), MAX_RUN_DETAIL_TEXT),
        "summary_mode": result.get("summary_mode"),
        "summary_error": result.get("summary_error"),
        "source_count": result.get("source_count"),
        "item_count": len(items),
        "error_count": len(errors),
        "items": [_digest_item_detail(item) for item in items[:MAX_RUN_DETAIL_ITEMS] if isinstance(item, dict)],
        "errors": [_source_error_detail(error) for error in errors[:MAX_RUN_DETAIL_ERRORS] if isinstance(error, dict)],
    }


def _digest_item_detail(item: dict) -> dict:
    return {
        "title": _truncate_text(item.get("title"), MAX_RUN_DETAIL_FIELD_TEXT),
        "summary": _truncate_text(item.get("summary"), MAX_RUN_DETAIL_FIELD_TEXT),
        "url": _truncate_text(item.get("link") or item.get("url") or item.get("source"), MAX_RUN_DETAIL_FIELD_TEXT),
        "published": _truncate_text(item.get("published"), 200),
        "type": _truncate_text(item.get("type"), 100),
    }


def _source_error_detail(error: dict) -> dict:
    return {
        "source": _truncate_text(error.get("source"), MAX_RUN_DETAIL_FIELD_TEXT),
        "error": _truncate_text(error.get("error"), 200),
    }


def _n8n_detail(result: dict) -> dict | None:
    n8n = _as_dict(result.get("n8n"))
    if not n8n:
        return None
    return {
        "status": n8n.get("status"),
        "notify": n8n.get("notify"),
        "webhook_id": n8n.get("webhook_id"),
        "path": n8n.get("path"),
        "status_code": n8n.get("status_code"),
        "message": _truncate_text(n8n.get("message"), MAX_RUN_DETAIL_FIELD_TEXT),
        "payload_keys": _as_list(n8n.get("payload_keys")),
        "response": _bounded_value(n8n.get("response")),
    }


def _failure_detail(log: dict) -> dict | None:
    if not log.get("error") and not log.get("message"):
        return None
    return {
        "error": _truncate_text(log.get("error"), 200),
        "message": _truncate_text(log.get("message"), MAX_RUN_DETAIL_FIELD_TEXT),
    }


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _truncate_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated>"


def _bounded_value(
    value: Any,
    depth: int = 0,
    *,
    max_depth: int = MAX_RUN_DETAIL_DEPTH,
    max_keys: int = MAX_RUN_DETAIL_KEYS,
    text_limit: int = MAX_RUN_DETAIL_TEXT,
    field_text_limit: int = MAX_RUN_DETAIL_FIELD_TEXT,
) -> Any:
    if depth >= max_depth:
        return "<truncated>"
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= max_keys:
                bounded["_truncated_keys"] = len(value) - max_keys
                break
            key_text = str(key)
            if _run_detail_secret_key(key_text):
                bounded[key_text] = "[REDACTED]"
            else:
                bounded[key_text] = _bounded_value(
                    child,
                    depth + 1,
                    max_depth=max_depth,
                    max_keys=max_keys,
                    text_limit=text_limit,
                    field_text_limit=field_text_limit,
                )
        return bounded
    if isinstance(value, list):
        bounded_items = [
            _bounded_value(
                item,
                depth + 1,
                max_depth=max_depth,
                max_keys=max_keys,
                text_limit=text_limit,
                field_text_limit=field_text_limit,
            )
            for item in value[:MAX_RUN_DETAIL_ITEMS]
        ]
        if len(value) > MAX_RUN_DETAIL_ITEMS:
            bounded_items.append({"_truncated_items": len(value) - MAX_RUN_DETAIL_ITEMS})
        return bounded_items
    if isinstance(value, str):
        limit = text_limit if depth <= 2 else field_text_limit
        return _truncate_text(value, limit)
    return value


def _run_detail_secret_key(key: str) -> bool:
    lower_key = key.lower()
    if lower_key in RUN_DETAIL_NON_SECRET_KEYS:
        return False
    return any(marker in lower_key for marker in RUN_DETAIL_SECRET_KEY_MARKERS)


def _approval_history_summary(approval: ApprovalModel) -> dict:
    return {
        "id": approval.id,
        "task_id": approval.task_id,
        "approval_level": approval.approval_level,
        "requested_by": approval.requested_by,
        "risk": approval.risk,
        "status": approval.status,
        "created_at": approval.created_at,
        "decided_at": approval.decided_at,
        "summary": _truncate_text(approval.summary, 500),
    }


def _approval_summary(
    approval: ApprovalModel,
    task: TaskModel | None = None,
    *,
    session: Session | None = None,
) -> dict:
    payload = {
        "id": approval.id,
        "task_id": approval.task_id,
        "approval_level": approval.approval_level,
        "requested_by": approval.requested_by,
        "risk": approval.risk,
        "status": approval.status,
        "created_at": approval.created_at,
        "decided_at": approval.decided_at,
        "summary": approval.summary[:280],
    }
    if task:
        payload["task"] = _approval_task_detail(task)
        payload["review"] = {
            "actions": _approval_actions(task),
            "failure_mode": _approval_failure_mode(task),
            "config_change": _approval_config_change(task),
        }
        if session:
            payload["review"]["config_diff"] = _approval_config_diff(session, approval, task)
    return payload


def _approval_task_detail(task: TaskModel) -> dict:
    config = task.config if isinstance(task.config, dict) else {}
    sources = config.get("sources") if isinstance(config.get("sources"), list) else []
    policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    return {
        "id": task.id,
        "name": task.name,
        "type": task.type,
        "enabled": task.enabled,
        "status": task.status,
        "approval_level": task.approval_level,
        "trigger": config.get("trigger") if isinstance(config.get("trigger"), dict) else {},
        "output": config.get("output") if isinstance(config.get("output"), dict) else {},
        "policy": redact_secrets(policy),
        "runtime": redact_secrets(runtime),
        "sources": redact_secrets(sources),
        "config": redact_secrets(config),
    }


def _approval_actions(task: TaskModel) -> list[str]:
    config = task.config if isinstance(task.config, dict) else {}
    trigger = config.get("trigger") if isinstance(config.get("trigger"), dict) else {}
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    sources = config.get("sources") if isinstance(config.get("sources"), list) else []
    actions = [f"Enable task {task.id} after approval"]
    if trigger.get("kind") == "schedule":
        actions.append(f"Schedule recurring execution with cron {trigger.get('cron')} in {trigger.get('timezone')}")
    actions.append(f"Run bounded worker handler {task.type}")
    if task.type == "topic_digest":
        actions.append(f"Fetch and summarize {len(sources)} configured sources as untrusted data")
    if output.get("channel") == "discord":
        mode = "dry-run Discord delivery" if runtime.get("dry_run", True) else "live Discord delivery"
        actions.append(f"Use {mode} to whitelisted target {output.get('target')}")
    return actions


def _approval_failure_mode(task: TaskModel) -> str:
    config = task.config if isinstance(task.config, dict) else {}
    policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    level = task.approval_level
    if level == "L1_NOTIFY_ONLY" and output.get("channel") == "discord":
        if runtime.get("dry_run", True):
            return "Dry-run output may be noisy or misleading, but no Discord message should be sent."
        return "A noisy, incomplete, or incorrect message could be sent to the whitelisted Discord target."
    if policy.get("allow_filesystem_write"):
        return "A bounded local write could create or update the configured file target incorrectly."
    if policy.get("allow_external_side_effects"):
        return "The configured external system could receive an incorrect but scoped action."
    if level == "L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE":
        return "Manual-only action; the automation API must not execute this approval automatically."
    return "The task could produce incorrect output or fail, but it remains bounded by its configured policy."


def _approval_config_change(task: TaskModel) -> dict:
    config = task.config if isinstance(task.config, dict) else {}
    return {
        "type": "current_task_config",
        "note": "This approval applies to the task configuration currently stored in the control plane.",
        "enabled_before_approval": task.enabled,
        "enabled_after_approval": True,
        "current_config": redact_secrets(config),
    }


def _approval_config_diff(session: Session, approval: ApprovalModel, task: TaskModel) -> dict:
    version = task_config_version_for_approval(session, approval.id)
    if version:
        return task_config_version_to_dict(session, version, include_config=False)
    return {
        "version": None,
        "change_type": "current_task_config",
        "approval_id": approval.id,
        "summary": "No approval-linked config version exists; showing diff from empty baseline to current config.",
        "diff": config_diff(None, task.config if isinstance(task.config, dict) else {}),
    }


def _audit_summary(audit: AuditEventModel | None) -> dict | None:
    if audit is None:
        return None
    return {
        "action": audit.action,
        "created_at": audit.created_at,
        "detail": _bounded_audit_detail(audit.detail),
    }


def _audit_event_detail(audit: AuditEventModel) -> dict:
    return {
        "id": audit.id,
        "actor_role": audit.actor_role,
        "action": audit.action,
        "resource_type": audit.resource_type,
        "resource_id": audit.resource_id,
        "detail": _bounded_audit_detail(audit.detail),
        "created_at": audit.created_at,
    }


def _bounded_audit_detail(detail: Any) -> Any:
    return _bounded_value(
        redact_secrets(detail),
        max_depth=MAX_AUDIT_DETAIL_DEPTH,
        max_keys=MAX_AUDIT_DETAIL_KEYS,
        text_limit=MAX_AUDIT_DETAIL_TEXT,
        field_text_limit=MAX_AUDIT_DETAIL_TEXT,
    )


DASHBOARD_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Yggy Operations</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #18202a;
      --muted: #5f6b7a;
      --line: #d9dee7;
      --ok: #0f7b4b;
      --warn: #9a5b00;
      --bad: #b42318;
      --accent: #2457c5;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #101418;
        --panel: #171d24;
        --text: #eef2f6;
        --muted: #a7b0bd;
        --line: #2b3542;
        --ok: #49c783;
        --warn: #e0a33a;
        --bad: #ff6b61;
        --accent: #8fb4ff;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header, .tabs, main {{ width: min(1180px, calc(100vw - 32px)); margin: 0 auto; }}
    header {{ padding: 24px 0 12px; display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; }}
    h1 {{ margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 13px; letter-spacing: 0; }}
    .tabs {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      gap: 6px;
      overflow-x: auto;
      padding: 8px 0 10px;
      background: color-mix(in srgb, var(--bg) 92%, transparent);
      backdrop-filter: blur(8px);
    }}
    .tab-button {{
      white-space: nowrap;
      color: var(--muted);
      padding: 8px 10px;
    }}
    .tab-button.active {{
      color: var(--text);
      border-color: var(--accent);
      background: color-mix(in srgb, var(--accent) 10%, var(--panel));
    }}
    .tab-count {{ color: var(--muted); margin-left: 4px; }}
    .view {{ display: none; }}
    .view.active {{ display: block; }}
    button {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 6px;
      padding: 8px 12px;
      cursor: pointer;
    }}
    button:hover {{ border-color: var(--accent); }}
    .link-button {{
      border: 0;
      background: transparent;
      color: var(--accent);
      padding: 0;
      text-decoration: underline;
      font: inherit;
    }}
    .link-button:hover {{ border-color: transparent; }}
    input, select {{
      width: min(360px, 100%);
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
    }}
    select {{ width: auto; min-width: 150px; }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: color-mix(in srgb, var(--bg) 70%, var(--panel));
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      max-height: 320px;
      overflow: auto;
    }}
    hr {{ border: 0; border-top: 1px solid var(--line); margin: 14px 0; }}
    .meta {{ color: var(--muted); font-size: 12px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }}
    .section {{ margin: 18px 0; }}
    .section-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .filter-bar {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin: 10px 0;
    }}
    .filter-bar input {{ width: min(320px, 100%); }}
    .filter-bar button {{ padding: 8px 10px; }}
    .panel, .metric, table {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric {{ padding: 12px; min-height: 72px; }}
    .metric .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
    .panel {{ padding: 14px; }}
    .status {{ display: inline-flex; gap: 6px; align-items: center; font-weight: 650; }}
    .dot {{ width: 9px; height: 9px; border-radius: 99px; background: var(--muted); display: inline-block; }}
    .ok .dot {{ background: var(--ok); }}
    .warn .dot {{ background: var(--warn); }}
    .bad .dot {{ background: var(--bad); }}
    .ok {{ color: var(--ok); }}
    .warn {{ color: var(--warn); }}
    .bad {{ color: var(--bad); }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; border: 0; min-width: 760px; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 650; }}
    tr:last-child td {{ border-bottom: 0; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .pill {{ display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; font-size: 12px; }}
    .empty {{ color: var(--muted); padding: 12px 0; }}
    .approval {{ display: grid; gap: 10px; }}
    .approval-head {{ display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; }}
    .approval-actions {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .approval-message {{ min-height: 18px; }}
    .danger {{ border-color: var(--bad); color: var(--bad); }}
    .detail-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .detail-block {{ min-width: 0; }}
    .detail-block.wide {{ grid-column: 1 / -1; }}
    .diff-list {{ margin: 6px 0 0; padding-left: 20px; }}
    .diff-list li {{ margin: 4px 0; overflow-wrap: anywhere; }}
    .digest-items {{ margin: 0; padding-left: 22px; }}
    .digest-items li {{ margin: 7px 0; }}
    .run-actions {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .run-actions button {{ padding: 6px 9px; }}
    .run-actions button:disabled {{ cursor: not-allowed; opacity: 0.55; }}
    .state-actions {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }}
    .state-actions button {{ padding: 6px 9px; }}
    .state-actions button:disabled {{ cursor: not-allowed; opacity: 0.55; }}
    .version-actions {{ margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }}
    .version-actions button {{ padding: 6px 9px; }}
    .version-actions button:disabled {{ cursor: not-allowed; opacity: 0.55; }}
    @media (max-width: 860px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .detail-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 560px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>{escape("Yggy Operations")}</h1>
      <div class="meta" id="generated">Loading status...</div>
    </div>
    <button id="refresh" type="button" title="Refresh status">Refresh</button>
  </header>
  <nav class="tabs" aria-label="Operations views">
    <button class="tab-button active" type="button" data-view-target="overview">Overview</button>
    <button class="tab-button" type="button" data-view-target="tasks">Tasks <span class="tab-count" data-count="tasks"></span></button>
    <button class="tab-button" type="button" data-view-target="runs">Runs <span class="tab-count" data-count="runs"></span></button>
    <button class="tab-button" type="button" data-view-target="approvals">Approvals <span class="tab-count" data-count="approvals"></span></button>
    <button class="tab-button" type="button" data-view-target="audit">Audit</button>
    <button class="tab-button" type="button" data-view-target="retention">Retention</button>
  </nav>
  <main>
    <section class="view active" data-view="overview">
      <section class="grid" id="metrics"></section>
      <section class="section panel" id="service"></section>
    </section>
    <section class="view" data-view="tasks">
      <section class="section">
        <div class="section-head">
          <div>
            <h2>Tasks</h2>
            <div class="meta" id="task-filter-summary">No filters applied.</div>
          </div>
        </div>
        <div class="filter-bar" aria-label="Task filters">
          <input id="task-filter-text" type="search" placeholder="Filter tasks" aria-label="Filter tasks">
          <select id="task-filter-state" aria-label="Task state">
            <option value="">All states</option>
            <option value="enabled">Enabled</option>
            <option value="disabled">Disabled</option>
            <option value="paused">Paused</option>
            <option value="pending_approval">Pending approval</option>
            <option value="rejected">Rejected</option>
          </select>
          <select id="task-filter-type" aria-label="Task type">
            <option value="">All types</option>
          </select>
          <button id="task-filter-clear" type="button">Clear</button>
        </div>
        <div class="table-wrap"><table id="tasks"></table></div>
        <div class="meta" id="task-action-status"></div>
      </section>
      <section class="section panel" id="task-detail">
        <h2>Task Detail</h2>
        <div class="empty">Select a task to inspect its redacted config, approval history, recent runs, and allowed actions.</div>
      </section>
    </section>
    <section class="view" data-view="runs">
      <section class="section">
        <div class="section-head">
          <div>
            <h2>Recent Runs</h2>
            <div class="meta" id="run-filter-summary">No filters applied.</div>
          </div>
        </div>
        <div class="filter-bar" aria-label="Run filters">
          <input id="run-filter-text" type="search" placeholder="Filter runs" aria-label="Filter runs">
          <select id="run-filter-status" aria-label="Run status">
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="dry_run">Dry-run</option>
          </select>
          <button id="run-filter-clear" type="button">Clear</button>
        </div>
        <div class="table-wrap"><table id="runs"></table></div>
      </section>
      <section class="section panel" id="run-detail">
        <h2>Run Detail</h2>
        <div class="empty">Select a recent run to inspect its digest, n8n response, notification decision, and Discord result.</div>
      </section>
    </section>
    <section class="view" data-view="approvals">
      <section class="section panel" id="approvals"></section>
    </section>
    <section class="view" data-view="audit">
      <section class="section panel">
        <div class="section-head">
          <div>
            <h2>Audit Events</h2>
            <div class="meta" id="audit-generated">Not loaded yet.</div>
          </div>
          <button id="audit-refresh" type="button">Refresh Audit</button>
        </div>
        <div class="filter-bar" aria-label="Audit filters">
          <input id="audit-filter-q" type="search" placeholder="Search actor, action, resource" aria-label="Search audit events">
          <input id="audit-filter-resource-id" type="search" placeholder="Resource id" aria-label="Audit resource id">
          <select id="audit-filter-actor" aria-label="Audit actor">
            <option value="">All actors</option>
            <option value="ops_dashboard">ops_dashboard</option>
            <option value="tool">tool</option>
            <option value="admin">admin</option>
            <option value="worker">worker</option>
          </select>
          <select id="audit-filter-action" aria-label="Audit action">
            <option value="">All actions</option>
            <option value="approval.approve">approval.approve</option>
            <option value="approval.reject">approval.reject</option>
            <option value="approval.request">approval.request</option>
            <option value="task.draft">task.draft</option>
            <option value="task.config.revert">task.config.revert</option>
            <option value="task.update">task.update</option>
            <option value="task.pause">task.pause</option>
            <option value="task.resume">task.resume</option>
            <option value="task.run">task.run</option>
            <option value="run.claim">run.claim</option>
            <option value="run.update">run.update</option>
            <option value="maintenance.retention.preview">maintenance.retention.preview</option>
            <option value="maintenance.retention.apply">maintenance.retention.apply</option>
            <option value="heartbeat.update">heartbeat.update</option>
          </select>
          <select id="audit-filter-resource-type" aria-label="Audit resource type">
            <option value="">All resources</option>
            <option value="task">task</option>
            <option value="run">run</option>
            <option value="approval">approval</option>
            <option value="service">service</option>
            <option value="topic">topic</option>
            <option value="maintenance">maintenance</option>
          </select>
          <button id="audit-filter-clear" type="button">Clear</button>
        </div>
        <div class="table-wrap"><table id="audit"></table></div>
      </section>
    </section>
    <section class="view" data-view="retention">
      <section class="section panel" id="retention"></section>
    </section>
  </main>
  <script>
    const text = value => value === null || value === undefined || value === '' ? 'n/a' : String(value);
    const esc = value => text(value).replace(/[&<>"']/g, char => ({{
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }}[char]));
    const shortId = value => value ? String(value).slice(0, 8) : 'n/a';
    const statusClass = value => value === true || value === 'ok' || value === 'completed' ? 'ok'
      : value === false || value === 'failed' || value === 'degraded' ? 'bad' : 'warn';
    function statusLabel(value, label) {{
      const cls = statusClass(value);
      return `<span class="status ${{cls}}"><span class="dot"></span>${{esc(label || text(value))}}</span>`;
    }}
    function metric(label, value, sub) {{
      return `<div class="metric"><div class="meta">${{esc(label)}}</div><div class="value">${{value}}</div><div class="meta">${{sub || ''}}</div></div>`;
    }}
    const jsonBlock = value => `<pre>${{esc(JSON.stringify(value || {{}}, null, 2))}}</pre>`;
    const byId = id => document.getElementById(id);
    function renderTable(id, headers, rows, emptyText = 'No rows match the current view.') {{
      const table = document.getElementById(id);
      table.innerHTML = `<thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join('')}}</tr></thead>`
        + `<tbody>${{rows.length ? rows.map(row => `<tr>${{row.map(cell => `<td>${{cell}}</td>`).join('')}}</tr>`).join('') : `<tr><td colspan="${{headers.length}}" class="empty">${{esc(emptyText)}}</td></tr>`}}</tbody>`;
    }}
    let lastStatusData = null;
    let activeView = 'overview';
    function showView(view) {{
      activeView = view;
      document.querySelectorAll('.view').forEach(section => {{
        section.classList.toggle('active', section.dataset.view === view);
      }});
      document.querySelectorAll('[data-view-target]').forEach(button => {{
        button.classList.toggle('active', button.dataset.viewTarget === view);
      }});
      if (view === 'audit') loadAudit();
    }}
    function wireViewTabs() {{
      document.querySelectorAll('[data-view-target]').forEach(button => {{
        button.addEventListener('click', () => showView(button.dataset.viewTarget));
      }});
    }}
    function setTabCount(name, value) {{
      const target = document.querySelector(`[data-count="${{name}}"]`);
      if (target) target.textContent = `(${{value}})`;
    }}
    const fieldValue = id => (byId(id)?.value || '').trim();
    const lower = value => text(value).toLowerCase();
    function matchesText(values, query) {{
      if (!query) return true;
      const haystack = values.map(value => lower(value)).join(' ');
      return haystack.includes(query.toLowerCase());
    }}
    function syncTaskTypeOptions(tasks) {{
      const select = byId('task-filter-type');
      const selected = select.value;
      const types = [...new Set(tasks.map(task => task.type).filter(Boolean))].sort();
      select.innerHTML = '<option value="">All types</option>' + types.map(type => `<option value="${{esc(type)}}">${{esc(type)}}</option>`).join('');
      select.value = types.includes(selected) ? selected : '';
    }}
    let selectedRunId = null;
    let selectedTaskId = null;
    const taskButton = task => `<button type="button" class="link-button" data-task-detail-id="${{esc(task.id)}}" title="${{esc(task.id)}}">${{esc(task.id)}}</button>`;
    const runButton = run => `<button type="button" class="link-button" data-run-id="${{esc(run.id)}}" title="${{esc(run.id)}}">${{esc(shortId(run.id))}}</button>`;
    function wireTaskDetailLinks() {{
      document.querySelectorAll('[data-task-detail-id]').forEach(button => {{
        button.addEventListener('click', () => loadTaskDetail(button.dataset.taskDetailId));
      }});
    }}
    function wireRunLinks() {{
      document.querySelectorAll('[data-run-id]').forEach(button => {{
        button.addEventListener('click', () => loadRunDetail(button.dataset.runId));
      }});
    }}
    const l2Plus = new Set(['L2_LOCAL_WRITE', 'L3_EXTERNAL_SIDE_EFFECT', 'L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE']);
    function taskRunButtons(task) {{
      const liveBlocked = !task.enabled || l2Plus.has(task.approval_level);
      const liveTitle = !task.enabled ? 'Task must be enabled for a live run'
        : l2Plus.has(task.approval_level) ? 'Live L2+ runs require the admin API'
        : 'Queue live run';
      return `<div class="run-actions">
        <button type="button" data-task-run="true" data-task-id="${{esc(task.id)}}" data-run-mode="dry_run" title="Queue dry-run">Dry run</button>
        <button type="button" data-task-run="true" data-task-id="${{esc(task.id)}}" data-run-mode="live" title="${{esc(liveTitle)}}"${{liveBlocked ? ' disabled' : ''}}>Live run</button>
      </div>`;
    }}
    function taskStateButtons(task) {{
      const stateBlocked = l2Plus.has(task.approval_level);
      if (task.enabled) {{
        const title = stateBlocked ? 'L2+ pauses require the admin API' : 'Pause task';
        return `<div class="state-actions"><button type="button" data-task-state="true" data-task-id="${{esc(task.id)}}" data-state-action="pause" title="${{esc(title)}}"${{stateBlocked ? ' disabled' : ''}}>Pause</button></div>`;
      }}
      const resumeBlocked = stateBlocked || task.status === 'pending_approval' || task.status === 'rejected';
      const title = stateBlocked ? 'L2+ resumes require the admin API'
        : task.status === 'pending_approval' ? 'Task is still pending approval'
        : task.status === 'rejected' ? 'Rejected task requires a new approval'
        : 'Resume task';
      return `<div class="state-actions"><button type="button" data-task-state="true" data-task-id="${{esc(task.id)}}" data-state-action="resume" title="${{esc(title)}}"${{resumeBlocked ? ' disabled' : ''}}>Resume</button></div>`;
    }}
    function wireTaskRunButtons() {{
      document.querySelectorAll('[data-task-run]').forEach(button => {{
        button.addEventListener('click', () => runTask(button));
      }});
    }}
    function wireTaskStateButtons() {{
      document.querySelectorAll('[data-task-state]').forEach(button => {{
        button.addEventListener('click', () => setTaskState(button));
      }});
    }}
    function wireTaskVersionRevertButtons() {{
      document.querySelectorAll('[data-task-version-revert]').forEach(button => {{
        button.addEventListener('click', () => revertTaskVersion(button));
      }});
    }}
    async function runTask(button) {{
      const taskId = button.dataset.taskId;
      const mode = button.dataset.runMode;
      const status = document.getElementById('task-action-status');
      button.disabled = true;
      status.textContent = `${{mode.replace('_', '-')}} request pending for ${{taskId}}...`;
      status.className = 'meta';
      try {{
        const response = await fetch(`/ops/tasks/${{encodeURIComponent(taskId)}}/run`, {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{'Content-Type': 'application/json', 'X-Yggy-Ops-Action': 'manual-run'}},
          body: JSON.stringify({{mode}}),
        }});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const body = await response.json();
        status.textContent = body.deduplicated
          ? `Run not queued: ${{body.reason}}; using ${{shortId(body.run_id)}}.`
          : `Queued ${{mode.replace('_', '-')}} run ${{shortId(body.run_id)}}.`;
        await refresh();
        if (body.run_id) await loadRunDetail(body.run_id);
      }} catch (error) {{
        status.textContent = error.message;
        status.className = 'meta bad';
      }} finally {{
        button.disabled = false;
      }}
    }}
    async function setTaskState(button) {{
      const taskId = button.dataset.taskId;
      const action = button.dataset.stateAction;
      const status = document.getElementById('task-action-status');
      button.disabled = true;
      status.textContent = `${{action}} request pending for ${{taskId}}...`;
      status.className = 'meta';
      try {{
        const response = await fetch(`/ops/tasks/${{encodeURIComponent(taskId)}}/${{action}}`, {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{'X-Yggy-Ops-Action': 'task-state'}},
        }});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const body = await response.json();
        status.textContent = `${{action === 'pause' ? 'Paused' : 'Resumed'}} ${{body.id || taskId}}.`;
        await refresh();
      }} catch (error) {{
        status.textContent = error.message;
        status.className = 'meta bad';
      }} finally {{
        button.disabled = false;
      }}
    }}
    async function revertTaskVersion(button) {{
      const taskId = button.dataset.taskId;
      const version = button.dataset.version;
      const status = document.getElementById('task-action-status');
      if (!window.confirm(`Create a disabled revert draft for ${{taskId}} from config version ${{version}}?`)) return;
      button.disabled = true;
      status.textContent = `revert request pending for ${{taskId}} from v${{version}}...`;
      status.className = 'meta';
      try {{
        const response = await fetch(`/ops/tasks/${{encodeURIComponent(taskId)}}/versions/${{encodeURIComponent(version)}}/revert`, {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{'Content-Type': 'application/json', 'X-Yggy-Ops-Action': 'version-revert'}},
          body: JSON.stringify({{reason: 'Reverted from ops dashboard'}}),
        }});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const body = await response.json();
        status.textContent = `Revert draft created as v${{body.new_version?.version}}. Approval ${{shortId(body.approval?.id)}} created. Nonce shown once: ${{body.approval_nonce}}`;
        await refresh();
        await loadTaskDetail(taskId);
      }} catch (error) {{
        status.textContent = error.message;
        status.className = 'meta bad';
      }} finally {{
        button.disabled = false;
      }}
    }}
    function taskMatchesFilters(task) {{
      const query = fieldValue('task-filter-text');
      const state = fieldValue('task-filter-state');
      const type = fieldValue('task-filter-type');
      const stateMatch = !state
        || (state === 'enabled' && task.enabled)
        || (state === 'disabled' && !task.enabled)
        || task.status === state;
      return stateMatch
        && (!type || task.type === type)
        && matchesText([task.id, task.name, task.type, task.status, task.approval_level, task.output?.target], query);
    }}
    function renderTasks() {{
      if (!lastStatusData) return;
      const tasks = lastStatusData.tasks || [];
      const filtered = tasks.filter(taskMatchesFilters);
      byId('task-filter-summary').textContent = `Showing ${{filtered.length}} of ${{tasks.length}} tasks.`;
      renderTable('tasks', ['Task', 'Type', 'State', 'Trigger', 'Output', 'Latest Run', 'Actions'], filtered.map(task => [
        `${{taskButton(task)}}<br><span class="meta">${{esc(task.name)}}</span>`,
        `<span class="pill">${{esc(task.type)}}</span><br><span class="meta">${{esc(task.approval_level)}}</span>`,
        `${{statusLabel(task.enabled, task.enabled ? 'enabled' : 'disabled')}}<br><span class="meta">status ${{esc(task.status)}}; dry run ${{task.dry_run}}</span>`,
        `<code>${{esc(task.trigger.cron)}}</code><br><span class="meta">${{esc(task.trigger.timezone)}}</span>`,
        `${{esc(task.output.channel)}}<br><span class="meta">${{esc(task.output.target)}}</span>`,
        task.latest_run ? `${{runButton(task.latest_run)}} ${{statusLabel(task.latest_run.status)}}<br><span class="meta">${{esc(task.latest_run.completed_at)}}</span>` : '<span class="meta">no runs</span>',
        `${{taskRunButtons(task)}}${{taskStateButtons(task)}}`,
      ]), 'No tasks match the current filters.');
      wireTaskDetailLinks();
      wireRunLinks();
      wireTaskRunButtons();
      wireTaskStateButtons();
    }}
    function actionLine(label, action) {{
      const item = action || {{}};
      return `<div>${{statusLabel(item.allowed === true, label)}}<br><span class="meta">${{esc(item.reason || 'n/a')}}</span></div>`;
    }}
    function approvalHistory(approvals) {{
      return approvals && approvals.length ? approvals.map(approval => `
        <div>
          <code>${{esc(approval.id)}}</code> ${{statusLabel(approval.status)}} <span class="pill">${{esc(approval.approval_level)}}</span><br>
          <span class="meta">requested by ${{esc(approval.requested_by)}} at ${{esc(approval.created_at)}}${{approval.decided_at ? `; decided ${{esc(approval.decided_at)}}` : ''}}</span><br>
          <span>${{esc(approval.summary)}}</span>
        </div>
      `).join('<hr>') : '<div class="empty">No approval history recorded for this task.</div>';
    }}
    function taskRecentRuns(runs) {{
      return runs && runs.length ? runs.map(run => `
        <div>
          ${{runButton(run)}} ${{statusLabel(run.status)}}<br>
          <span class="meta">created ${{esc(run.created_at)}}; completed ${{esc(run.completed_at)}}; notification ${{esc(run.notification?.sent)}}</span>
        </div>
      `).join('<hr>') : '<div class="empty">No runs recorded for this task.</div>';
    }}
    function inlineJson(value) {{
      return `<code>${{esc(JSON.stringify(value))}}</code>`;
    }}
    function configDiffSummary(diff) {{
      const counts = diff?.counts || {{}};
      return `added ${{counts.added || 0}}, removed ${{counts.removed || 0}}, changed ${{counts.changed || 0}}${{diff?.truncated ? '; truncated' : ''}}`;
    }}
    function configDiffList(diff) {{
      if (!diff) return '<div class="empty">No config diff recorded.</div>';
      const rows = [];
      (diff.added || []).forEach(item => rows.push(`<li><strong>added</strong> <code>${{esc(item.path)}}</code>: ${{inlineJson(item.after)}}</li>`));
      (diff.removed || []).forEach(item => rows.push(`<li><strong>removed</strong> <code>${{esc(item.path)}}</code>: ${{inlineJson(item.before)}}</li>`));
      (diff.changed || []).forEach(item => rows.push(`<li><strong>changed</strong> <code>${{esc(item.path)}}</code>: ${{inlineJson(item.before)}} -> ${{inlineJson(item.after)}}</li>`));
      return rows.length ? `<ul class="diff-list">${{rows.join('')}}</ul>` : '<div class="empty">No config field changes in this version.</div>';
    }}
    function configVersionHistory(versions) {{
      return versions && versions.length ? versions.map((version, index) => `
        <details ${{index === 0 ? 'open' : ''}}>
          <summary>
            v${{esc(version.version)}} ${{esc(version.change_type)}} by ${{esc(version.actor_role)}}
            <span class="meta">${{esc(version.created_at)}}</span>
          </summary>
          <div class="meta">approval ${{esc(version.approval_id)}}; diff ${{configDiffSummary(version.diff)}}</div>
          ${{version.summary ? `<div>${{esc(version.summary)}}</div>` : ''}}
          ${{configDiffList(version.diff)}}
          ${{index === 0 ? '<div class="meta">Current version cannot be reverted to itself.</div>' : `
            <div class="version-actions">
              <button type="button" class="danger" data-task-version-revert="true" data-task-id="${{esc(version.task_id)}}" data-version="${{esc(version.version)}}" title="Create disabled draft from this version">Revert to v${{esc(version.version)}}</button>
            </div>
          `}}
        </details>
      `).join('<hr>') : '<div class="empty">No config version snapshots recorded for this task.</div>';
    }}
    function renderTaskDetail(data) {{
      const task = data.task || {{}};
      const actions = data.allowed_actions || {{}};
      const approvals = data.approvals || [];
      const runs = data.recent_runs || [];
      const versions = data.config_versions || [];
      byId('task-detail').innerHTML = `
        <h2>Task Detail</h2>
        <div class="meta">
          <code>${{esc(task.id)}}</code> - ${{statusLabel(task.enabled, task.enabled ? 'enabled' : 'disabled')}} -
          status ${{esc(task.status)}} - approval ${{esc(task.approval_level)}} - updated ${{esc(task.updated_at)}}
        </div>
        <div class="detail-grid section">
          <div class="detail-block">
            <h3>Allowed Actions</h3>
            <div class="approval">
              ${{actionLine('dry run', actions.dry_run)}}
              ${{actionLine('live run', actions.live_run)}}
              ${{actionLine('pause', actions.pause)}}
              ${{actionLine('resume', actions.resume)}}
            </div>
          </div>
          <div class="detail-block">
            <h3>Task Summary</h3>
            <div><strong>${{esc(task.name)}}</strong></div>
            <div class="meta">type ${{esc(task.type)}}; dry run ${{esc(task.dry_run)}}</div>
            <div class="meta">cron <code>${{esc(task.trigger?.cron)}}</code> in ${{esc(task.trigger?.timezone)}}</div>
            <div class="meta">output ${{esc(task.output?.channel)}} / ${{esc(task.output?.target)}}</div>
          </div>
          <div class="detail-block">
            <h3>Approval History</h3>
            ${{approvalHistory(approvals)}}
          </div>
          <div class="detail-block">
            <h3>Recent Runs</h3>
            ${{taskRecentRuns(runs)}}
          </div>
          <div class="detail-block wide">
            <h3>Config Version History</h3>
            ${{configVersionHistory(versions)}}
          </div>
          <div class="detail-block wide">
            <h3>Redacted Config</h3>
            ${{jsonBlock(data.config)}}
          </div>
        </div>
      `;
      wireRunLinks();
      wireTaskVersionRevertButtons();
    }}
    async function loadTaskDetail(taskId) {{
      selectedTaskId = taskId;
      showView('tasks');
      const panel = byId('task-detail');
      panel.innerHTML = '<h2>Task Detail</h2><div class="empty">Loading task detail...</div>';
      try {{
        const response = await fetch(`/ops/tasks/${{encodeURIComponent(taskId)}}`, {{credentials: 'same-origin'}});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        renderTaskDetail(await response.json());
      }} catch (error) {{
        panel.innerHTML = `<h2>Task Detail</h2><div class="bad">Unable to load task detail: ${{esc(error.message)}}</div>`;
      }}
    }}
    function runMatchesFilters(run) {{
      const query = fieldValue('run-filter-text');
      const status = fieldValue('run-filter-status');
      const statusMatch = !status
        || run.status === status
        || (status === 'queued' && String(run.status || '').startsWith('queued'))
        || (status === 'running' && String(run.status || '').startsWith('running'))
        || (status === 'completed' && String(run.status || '').startsWith('completed'))
        || (status === 'dry_run' && String(run.status || '').includes('dry_run'));
      return statusMatch
        && matchesText([run.id, run.task_id, run.status, run.result_status, run.notification?.target, run.notification?.transport], query);
    }}
    function renderRuns() {{
      if (!lastStatusData) return;
      const runs = lastStatusData.recent_runs || [];
      const filtered = runs.filter(runMatchesFilters);
      byId('run-filter-summary').textContent = `Showing ${{filtered.length}} of ${{runs.length}} recent runs.`;
      renderTable('runs', ['Run', 'Task', 'Status', 'Result', 'Notification', 'Completed'], filtered.map(run => [
        runButton(run),
        `<code>${{esc(run.task_id)}}</code>`,
        statusLabel(run.status),
        `${{esc(run.result_status)}}${{run.failed_count !== null && run.failed_count !== undefined ? `<br><span class="meta">failed checks ${{esc(run.failed_count)}}</span>` : ''}}`,
        `${{run.notification.sent === true ? 'sent' : run.notification.sent === false ? 'not sent' : 'n/a'}}<br><span class="meta">${{esc(run.notification.target || run.notification.transport)}}</span>`,
        esc(run.completed_at),
      ]), 'No runs match the current filters.');
      wireRunLinks();
    }}
    function digestItems(items) {{
      return items && items.length ? `<ol class="digest-items">${{items.map(item => `
        <li>
          <strong>${{esc(item.title)}}</strong><br>
          <span>${{esc(item.summary)}}</span><br>
          <span class="meta">${{esc(item.published)}} ${{esc(item.type)}} ${{item.url ? `- ${{esc(item.url)}}` : ''}}</span>
        </li>
      `).join('')}}</ol>` : '<div class="empty">No digest items recorded.</div>';
    }}
    function renderRunDetail(data) {{
      const run = data.run || {{}};
      const task = data.task || {{}};
      const digest = data.digest;
      const n8n = data.n8n;
      const decision = data.notification_decision || {{}};
      const notification = data.notification || null;
      const failure = data.failure || null;
      document.getElementById('run-detail').innerHTML = `
        <h2>Run Detail</h2>
        <div class="meta"><code>${{esc(run.id)}}</code> for <code>${{esc(run.task_id || task.id)}}</code> - ${{statusLabel(run.status)}} - completed ${{esc(run.completed_at)}}</div>
        ${{failure ? `<div class="section bad"><strong>Failure</strong><br>${{esc(failure.error)}} ${{esc(failure.message)}}</div>` : ''}}
        <div class="detail-grid section">
          <div class="detail-block wide">
            <h3>Digest</h3>
            ${{digest ? `
              <div class="meta">status ${{esc(digest.status)}}; mode ${{esc(digest.summary_mode)}}; items ${{esc(digest.item_count)}}; errors ${{esc(digest.error_count)}}; sources ${{esc(digest.source_count)}}</div>
              <pre>${{esc(digest.message || '')}}</pre>
              ${{digestItems(digest.items)}}
              ${{digest.errors && digest.errors.length ? `<h3>Source Errors</h3>${{jsonBlock(digest.errors)}}` : ''}}
            ` : '<div class="empty">No topic digest result recorded for this run.</div>'}}
          </div>
          <div class="detail-block">
            <h3>n8n Response</h3>
            ${{n8n ? `
              <div class="meta">webhook <code>${{esc(n8n.webhook_id)}}</code>; status ${{esc(n8n.status)}}; HTTP ${{esc(n8n.status_code)}}</div>
              <div>${{esc(n8n.message)}}</div>
              ${{jsonBlock(n8n.response || {{payload_keys: n8n.payload_keys}})}}
            ` : '<div class="empty">No n8n response recorded for this run.</div>'}}
          </div>
          <div class="detail-block">
            <h3>Notification Decision</h3>
            ${{jsonBlock(decision)}}
          </div>
          <div class="detail-block">
            <h3>Discord Result</h3>
            ${{notification ? jsonBlock(notification) : '<div class="empty">No Discord send result recorded.</div>'}}
          </div>
        </div>
      `;
    }}
    async function loadRunDetail(runId) {{
      selectedRunId = runId;
      showView('runs');
      const panel = document.getElementById('run-detail');
      panel.innerHTML = '<h2>Run Detail</h2><div class="empty">Loading run detail...</div>';
      try {{
        const response = await fetch(`/ops/runs/${{encodeURIComponent(runId)}}`, {{credentials: 'same-origin'}});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        renderRunDetail(await response.json());
      }} catch (error) {{
        panel.innerHTML = `<h2>Run Detail</h2><div class="bad">Unable to load run detail: ${{esc(error.message)}}</div>`;
      }}
    }}
    function renderApprovals(approvals) {{
      const container = document.getElementById('approvals');
      container.innerHTML = '<h2>Pending Approvals</h2>' + (
        approvals.length ? approvals.map(item => {{
          const review = item.review || {{}};
          const task = item.task || {{}};
          const actions = review.actions || [];
          return `<div class="approval">
            <div class="approval-head">
              <div>
                <code>${{esc(item.id)}}</code> for <code>${{esc(item.task_id)}}</code>
                <span class="pill">${{esc(item.approval_level)}}</span>
              </div>
              <span class="meta">requested by ${{esc(item.requested_by)}} at ${{esc(item.created_at)}}</span>
            </div>
            <div class="meta">${{esc(item.summary)}}</div>
            <div><strong>Actions</strong><br>${{actions.map(action => `- ${{esc(action)}}`).join('<br>') || '<span class="meta">n/a</span>'}}</div>
            <div><strong>Worst-case failure mode</strong><br>${{esc(review.failure_mode)}}</div>
            <div><strong>Config change</strong><br>
              <span class="meta">enabled before approval: ${{esc(review.config_change?.enabled_before_approval)}}; enabled after approval: ${{esc(review.config_change?.enabled_after_approval)}}</span>
            </div>
            ${{review.config_diff ? `<div><strong>Config diff</strong><br>
              <span class="meta">version ${{esc(review.config_diff.version)}}; ${{configDiffSummary(review.config_diff.diff)}}</span>
              ${{configDiffList(review.config_diff.diff)}}
            </div>` : ''}}
            <details>
              <summary>Task config</summary>
              ${{jsonBlock(task.config)}}
            </details>
            <div class="approval-actions">
              <input type="password" autocomplete="off" placeholder="Approval nonce" aria-label="Approval nonce">
              <button type="button" data-approval-action="approve" data-approval-id="${{esc(item.id)}}">Approve</button>
              <button type="button" class="danger" data-approval-action="reject" data-approval-id="${{esc(item.id)}}">Reject</button>
              <span class="meta approval-message"></span>
            </div>
          </div>`;
        }}).join('<hr>')
        : '<div class="empty">No pending approvals.</div>'
      );
      container.querySelectorAll('[data-approval-action]').forEach(button => {{
        button.addEventListener('click', () => decideApproval(button));
      }});
    }}
    async function decideApproval(button) {{
      const approvalId = button.dataset.approvalId;
      const action = button.dataset.approvalAction;
      const panel = button.closest('.approval');
      const message = panel.querySelector('.approval-message');
      const input = panel.querySelector('input');
      const body = action === 'approve' ? {{nonce: input.value}} : {{reason: 'Rejected from ops dashboard'}};
      if (action === 'approve' && !input.value) {{
        message.textContent = 'Approval nonce is required.';
        message.className = 'meta approval-message bad';
        return;
      }}
      button.disabled = true;
      message.textContent = `${{action}} pending...`;
      message.className = 'meta approval-message';
      try {{
        const response = await fetch(`/ops/approvals/${{encodeURIComponent(approvalId)}}/${{action}}`, {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{'Content-Type': 'application/json', 'X-Yggy-Ops-Action': 'approval-decision'}},
          body: JSON.stringify(body),
        }});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        await refresh();
      }} catch (error) {{
        message.textContent = error.message;
        message.className = 'meta approval-message bad';
      }} finally {{
        button.disabled = false;
      }}
    }}
    async function loadStatus() {{
      const response = await fetch('/ops/status', {{credentials: 'same-origin'}});
      if (!response.ok) throw new Error(`status ${{response.status}}`);
      const data = await response.json();
      lastStatusData = data;
      document.getElementById('generated').textContent = `Generated ${{new Date(data.generated_at).toLocaleString()}}`;
      setTabCount('tasks', data.counts.tasks);
      setTabCount('runs', data.recent_runs.length);
      setTabCount('approvals', data.counts.pending_approvals);
      document.getElementById('metrics').innerHTML = [
        metric('Service', statusLabel(data.service.status), `worker age ${{text(data.service.worker.age_seconds)}}s`),
        metric('Tasks', data.counts.tasks, `${{data.counts.enabled_tasks}} enabled`),
        metric('Active Runs', data.counts.active_runs, 'queued or running'),
        metric('Pending Approvals', data.counts.pending_approvals, 'local approval only'),
      ].join('');
      document.getElementById('service').innerHTML = `
        <h2>Service Health</h2>
        <div>Database: ${{statusLabel(data.service.database.connected, data.service.database.connected ? 'connected' : 'degraded')}}</div>
        <div>Worker: ${{statusLabel(data.service.worker.ok, data.service.worker.status)}} <span class="meta">last seen ${{text(data.service.worker.last_seen_at)}}</span></div>
      `;
      syncTaskTypeOptions(data.tasks || []);
      renderTasks();
      renderRuns();
      if (selectedTaskId && activeView === 'tasks') loadTaskDetail(selectedTaskId);
      if (selectedRunId && activeView === 'runs') loadRunDetail(selectedRunId);
      renderApprovals(data.pending_approvals);
      const latestRetention = data.retention.latest;
      document.getElementById('retention').innerHTML = `
        <h2>Retention</h2>
        <div class="meta">Runs ${{data.retention.policy.run_retention_days}}d, audit ${{data.retention.policy.audit_retention_days}}d, temporary tasks ${{data.retention.policy.temp_task_retention_hours}}h</div>
        ${{latestRetention ? `<div>Latest: <code>${{latestRetention.action}}</code> at ${{text(latestRetention.created_at)}}</div>` : '<div class="empty">No cleanup recorded yet.</div>'}}
      `;
    }}
    async function loadAudit() {{
      const generated = document.getElementById('audit-generated');
      generated.textContent = 'Loading audit events...';
      try {{
        const params = new URLSearchParams({{limit: '50'}});
        const auditFilters = {{
          q: fieldValue('audit-filter-q'),
          resource_id: fieldValue('audit-filter-resource-id'),
          actor_role: fieldValue('audit-filter-actor'),
          action: fieldValue('audit-filter-action'),
          resource_type: fieldValue('audit-filter-resource-type'),
        }};
        Object.entries(auditFilters).forEach(([key, value]) => {{
          if (value) params.set(key, value);
        }});
        const response = await fetch(`/ops/audit?${{params.toString()}}`, {{credentials: 'same-origin'}});
        if (!response.ok) throw new Error(`status ${{response.status}}`);
        const data = await response.json();
        generated.textContent = `Generated ${{new Date(data.generated_at).toLocaleString()}}; showing ${{data.events.length}} events.`;
        renderTable('audit', ['Time', 'Actor', 'Action', 'Resource', 'Detail'], data.events.map(event => [
          esc(event.created_at),
          `<span class="pill">${{esc(event.actor_role)}}</span>`,
          `<code>${{esc(event.action)}}</code>`,
          `${{esc(event.resource_type)}}<br><code>${{esc(event.resource_id)}}</code>`,
          jsonBlock(event.detail),
        ]));
      }} catch (error) {{
        generated.textContent = `Unable to load audit events: ${{error.message}}`;
      }}
    }}
    function wireFilters() {{
      ['task-filter-text', 'task-filter-state', 'task-filter-type'].forEach(id => {{
        byId(id).addEventListener('input', renderTasks);
        byId(id).addEventListener('change', renderTasks);
      }});
      byId('task-filter-clear').addEventListener('click', () => {{
        byId('task-filter-text').value = '';
        byId('task-filter-state').value = '';
        byId('task-filter-type').value = '';
        renderTasks();
      }});
      ['run-filter-text', 'run-filter-status'].forEach(id => {{
        byId(id).addEventListener('input', renderRuns);
        byId(id).addEventListener('change', renderRuns);
      }});
      byId('run-filter-clear').addEventListener('click', () => {{
        byId('run-filter-text').value = '';
        byId('run-filter-status').value = '';
        renderRuns();
      }});
      ['audit-filter-q', 'audit-filter-resource-id', 'audit-filter-actor', 'audit-filter-action', 'audit-filter-resource-type'].forEach(id => {{
        byId(id).addEventListener('change', loadAudit);
      }});
      byId('audit-filter-q').addEventListener('input', debounce(loadAudit, 350));
      byId('audit-filter-resource-id').addEventListener('input', debounce(loadAudit, 350));
      byId('audit-filter-clear').addEventListener('click', () => {{
        ['audit-filter-q', 'audit-filter-resource-id', 'audit-filter-actor', 'audit-filter-action', 'audit-filter-resource-type'].forEach(id => {{
          byId(id).value = '';
        }});
        loadAudit();
      }});
    }}
    function debounce(fn, wait) {{
      let timeout;
      return () => {{
        clearTimeout(timeout);
        timeout = setTimeout(fn, wait);
      }};
    }}
    async function refresh() {{
      try {{
        await loadStatus();
        if (activeView === 'audit') await loadAudit();
      }}
      catch (error) {{ document.getElementById('generated').textContent = `Unable to load status: ${{error.message}}`; }}
    }}
    document.getElementById('refresh').addEventListener('click', refresh);
    document.getElementById('audit-refresh').addEventListener('click', loadAudit);
    wireViewTabs();
    wireFilters();
    refresh();
    setInterval(refresh, 30000);
  </script>
</body>
</html>
"""
