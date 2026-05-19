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
from app.models import (
    ApprovalModel,
    AuditEventModel,
    CapabilityProposalModel,
    HeartbeatModel,
    RunModel,
    SourceProposalModel,
    TaskChangeProposalModel,
    TaskModel,
    utcnow,
)
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
from app.services.task_change_service import (
    TaskChangeProposalError,
    apply_task_change_proposal,
    approve_task_change_proposal,
    proposal_to_dict,
    reject_task_change_proposal,
)
from app.services.capability_proposal_service import (
    CapabilityProposalError,
    capability_proposal_to_dict,
    close_capability_proposal,
    create_implementation_plan,
    implementation_plan_for_proposal,
    mark_implementation_plan_status,
)
from app.services.source_proposal_service import (
    SourceProposalError,
    apply_source_proposal,
    approve_source_proposal_from_ops,
    reject_source_proposal,
    source_proposal_to_dict,
)
from app.services.validation_service import redact_secrets

router = APIRouter(tags=["ops"])
basic_security = HTTPBasic(auto_error=False)
OPS_ACTION_HEADER = "approval-decision"
OPS_RUN_ACTION_HEADER = "manual-run"
OPS_TASK_STATE_ACTION_HEADER = "task-state"
OPS_VERSION_REVERT_ACTION_HEADER = "version-revert"
OPS_TASK_CHANGE_ACTION_HEADER = "task-change-proposal"
OPS_CAPABILITY_PROPOSAL_ACTION_HEADER = "capability-proposal"
OPS_SOURCE_PROPOSAL_ACTION_HEADER = "source-proposal"
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
PROPOSAL_CHANGE_TYPES = {"draft", "update", "revert_draft", "approval_request"}
MIN_OPS_PAGE_SIZE = 5
DEFAULT_OPS_PAGE_SIZE = 10
MAX_OPS_PAGE_SIZE = 100


class OpsApprovalDecision(BaseModel):
    nonce: str = Field(min_length=8, max_length=256)


class OpsApprovalRejection(BaseModel):
    reason: str = Field(default="", max_length=500)


class OpsTaskRunRequest(BaseModel):
    mode: Literal["dry_run", "live"] = "dry_run"


class OpsTaskVersionRevertRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class OpsCapabilityProposalDecision(BaseModel):
    reason: str = Field(default="", max_length=1000)


class OpsSourceProposalDecision(BaseModel):
    reason: str = Field(default="", max_length=1000)


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


def require_ops_task_change_action_header(
    x_yggy_ops_action: str | None = Header(default=None, alias="X-Yggy-Ops-Action"),
) -> None:
    if x_yggy_ops_action != OPS_TASK_CHANGE_ACTION_HEADER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing ops task change action header")


def require_ops_capability_proposal_action_header(
    x_yggy_ops_action: str | None = Header(default=None, alias="X-Yggy-Ops-Action"),
) -> None:
    if x_yggy_ops_action != OPS_CAPABILITY_PROPOSAL_ACTION_HEADER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing ops capability proposal action header")


def require_ops_source_proposal_action_header(
    x_yggy_ops_action: str | None = Header(default=None, alias="X-Yggy-Ops-Action"),
) -> None:
    if x_yggy_ops_action != OPS_SOURCE_PROPOSAL_ACTION_HEADER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing ops source proposal action header")


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
    open_task_change_proposals = (
        session.query(TaskChangeProposalModel)
        .filter(TaskChangeProposalModel.status.in_(["pending", "approved"]))
        .order_by(TaskChangeProposalModel.created_at.desc())
        .limit(20)
        .all()
    )
    pending_capability_proposals = (
        session.query(CapabilityProposalModel)
        .filter(CapabilityProposalModel.status == "pending")
        .order_by(CapabilityProposalModel.created_at.desc())
        .limit(20)
        .all()
    )
    open_source_proposals = (
        session.query(SourceProposalModel)
        .filter(SourceProposalModel.status.in_(["pending", "approved"]))
        .order_by(SourceProposalModel.created_at.desc())
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

    pending_approval_summaries = []
    pending_proposals = []
    pending_general_approvals = []
    for approval in pending_approvals:
        summary = _approval_summary(approval, session.get(TaskModel, approval.task_id), session=session)
        pending_approval_summaries.append(summary)
        if _approval_is_config_proposal(session, approval):
            pending_proposals.append(summary)
        else:
            pending_general_approvals.append(summary)
    pending_task_changes = [
        _task_change_proposal_summary(proposal, include_configs=False)
        for proposal in open_task_change_proposals
        if proposal.status == "pending"
    ]
    approved_task_changes = [
        _task_change_proposal_summary(proposal, include_configs=False)
        for proposal in open_task_change_proposals
        if proposal.status == "approved"
    ]
    pending_source_proposals = [
        _source_proposal_summary(proposal)
        for proposal in open_source_proposals
        if proposal.status == "pending"
    ]
    approved_source_proposals = [
        _source_proposal_summary(proposal)
        for proposal in open_source_proposals
        if proposal.status == "approved"
    ]

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
            "pending_proposals": len(pending_proposals),
            "pending_general_approvals": len(pending_general_approvals),
            "pending_task_change_proposals": len(pending_task_changes),
            "approved_task_change_proposals": len(approved_task_changes),
            "open_task_change_proposals": len(pending_task_changes) + len(approved_task_changes),
            "pending_capability_proposals": len(pending_capability_proposals),
            "pending_source_proposals": len(pending_source_proposals),
            "approved_source_proposals": len(approved_source_proposals),
            "open_source_proposals": len(pending_source_proposals) + len(approved_source_proposals),
            "pending_reviews": (
                len(pending_approvals)
                + len(pending_task_changes)
                + len(pending_capability_proposals)
                + len(pending_source_proposals)
                + len(approved_source_proposals)
            ),
            "active_runs": len(active_runs),
        },
        "tasks": [_task_summary(task, latest_by_task.get(task.id)) for task in tasks],
        "recent_runs": [_run_summary(run) for run in recent_runs[:10]],
        "pending_approvals": pending_approval_summaries,
        "pending_proposals": pending_proposals,
        "pending_general_approvals": pending_general_approvals,
        "pending_task_change_proposals": pending_task_changes,
        "approved_task_change_proposals": approved_task_changes,
        "open_task_change_proposals": pending_task_changes + approved_task_changes,
        "pending_capability_proposals": [
            _capability_proposal_summary(session, proposal) for proposal in pending_capability_proposals
        ],
        "pending_source_proposals": pending_source_proposals,
        "approved_source_proposals": approved_source_proposals,
        "open_source_proposals": pending_source_proposals + approved_source_proposals,
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


@router.get("/ops/runs", include_in_schema=False)
def ops_runs(
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_OPS_PAGE_SIZE, ge=MIN_OPS_PAGE_SIZE, le=MAX_OPS_PAGE_SIZE),
    sort_by: Literal["created_at", "completed_at", "task_id", "status", "id"] = Query(default="created_at"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    q: str | None = Query(default=None, min_length=1, max_length=128),
    task_id: str | None = Query(default=None, min_length=1, max_length=128),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=64),
    notification_sent: Literal["true", "false"] | None = Query(default=None),
) -> dict:
    query = session.query(RunModel)
    if task_id:
        query = query.filter(RunModel.task_id.ilike(f"%{task_id}%"))
    if status_filter:
        if status_filter in {"queued", "running", "completed"}:
            query = query.filter(RunModel.status.ilike(f"{status_filter}%"))
        elif status_filter == "dry_run":
            query = query.filter(RunModel.status.ilike("%dry_run%"))
        else:
            query = query.filter(RunModel.status == status_filter)
    if q:
        query = query.filter(
            or_(
                RunModel.id.ilike(f"%{q}%"),
                RunModel.task_id.ilike(f"%{q}%"),
                RunModel.status.ilike(f"%{q}%"),
            )
        )

    sort_columns = {
        "created_at": RunModel.created_at,
        "completed_at": RunModel.completed_at,
        "task_id": RunModel.task_id,
        "status": RunModel.status,
        "id": RunModel.id,
    }
    sort_expression = sort_columns[sort_by].asc() if sort_dir == "asc" else sort_columns[sort_by].desc()
    ordered_query = query.order_by(sort_expression, RunModel.created_at.desc(), RunModel.id.asc())
    matched_runs = ordered_query.all()
    if notification_sent is not None:
        expected_sent = notification_sent == "true"
        matched_runs = [run for run in matched_runs if _run_notification_sent(run) is expected_sent]
    total = len(matched_runs)
    offset = _pagination_offset(page, page_size)
    runs = matched_runs[offset : offset + page_size]
    return {
        "generated_at": utcnow(),
        "page": page,
        "page_size": page_size,
        "filters": {
            "q": q,
            "task_id": task_id,
            "status": status_filter,
            "notification_sent": notification_sent,
        },
        "sort": {"by": sort_by, "dir": sort_dir},
        "pagination": _pagination(page, page_size, total, len(runs)),
        "summary": _run_collection_summary(matched_runs),
        "runs": [_run_summary(run) for run in runs],
    }


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
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_OPS_PAGE_SIZE, ge=MIN_OPS_PAGE_SIZE, le=MAX_OPS_PAGE_SIZE),
    sort_by: Literal["created_at", "actor_role", "action", "resource_type", "resource_id", "id"] = Query(default="created_at"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
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
    total = query.count()
    sort_columns = {
        "created_at": AuditEventModel.created_at,
        "actor_role": AuditEventModel.actor_role,
        "action": AuditEventModel.action,
        "resource_type": AuditEventModel.resource_type,
        "resource_id": AuditEventModel.resource_id,
        "id": AuditEventModel.id,
    }
    sort_expression = sort_columns[sort_by].asc() if sort_dir == "asc" else sort_columns[sort_by].desc()
    events = (
        query.order_by(sort_expression, AuditEventModel.created_at.desc(), AuditEventModel.id.asc())
        .offset(_pagination_offset(page, page_size))
        .limit(page_size)
        .all()
    )
    return {
        "generated_at": utcnow(),
        "page": page,
        "page_size": page_size,
        "limit": page_size,
        "filters": {
            "actor_role": actor_role,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "q": q,
        },
        "sort": {"by": sort_by, "dir": sort_dir},
        "pagination": _pagination(page, page_size, total, len(events)),
        "events": [_audit_event_detail(event) for event in events],
    }


@router.get("/ops/reviews", include_in_schema=False)
def ops_reviews(
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
    kind: Literal["all", "proposals", "approvals"] = Query(default="all"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_OPS_PAGE_SIZE, ge=MIN_OPS_PAGE_SIZE, le=MAX_OPS_PAGE_SIZE),
    q: str | None = Query(default=None, min_length=1, max_length=128),
    task_id: str | None = Query(default=None, min_length=1, max_length=128),
    approval_level: str | None = Query(default=None, min_length=1, max_length=64),
    requested_by: str | None = Query(default=None, min_length=1, max_length=128),
    change_type: str | None = Query(default=None, min_length=1, max_length=64),
) -> dict:
    query = session.query(ApprovalModel).filter(ApprovalModel.status == "pending")
    if task_id:
        query = query.filter(ApprovalModel.task_id.ilike(f"%{task_id}%"))
    if approval_level:
        query = query.filter(ApprovalModel.approval_level == approval_level)
    if requested_by:
        query = query.filter(ApprovalModel.requested_by.ilike(f"%{requested_by}%"))
    if q:
        query = query.filter(
            or_(
                ApprovalModel.id.ilike(f"%{q}%"),
                ApprovalModel.task_id.ilike(f"%{q}%"),
                ApprovalModel.requested_by.ilike(f"%{q}%"),
                ApprovalModel.approval_level.ilike(f"%{q}%"),
                ApprovalModel.summary.ilike(f"%{q}%"),
                ApprovalModel.risk.ilike(f"%{q}%"),
            )
        )

    matched = []
    for approval in query.order_by(ApprovalModel.created_at.desc()).all():
        approval_change_type = _approval_change_type(session, approval)
        is_proposal = approval_change_type in PROPOSAL_CHANGE_TYPES
        if kind == "proposals" and not is_proposal:
            continue
        if kind == "approvals" and is_proposal:
            continue
        if change_type and approval_change_type != change_type:
            continue
        matched.append(approval)

    offset = _pagination_offset(page, page_size)
    selected = matched[offset : offset + page_size]
    return {
        "generated_at": utcnow(),
        "kind": kind,
        "page": page,
        "page_size": page_size,
        "limit": page_size,
        "filters": {
            "q": q,
            "task_id": task_id,
            "approval_level": approval_level,
            "requested_by": requested_by,
            "change_type": change_type,
        },
        "counts": {"matched": len(matched), "returned": len(selected)},
        "pagination": _pagination(page, page_size, len(matched), len(selected)),
        "reviews": [_approval_summary(approval, session.get(TaskModel, approval.task_id), session=session) for approval in selected],
    }


@router.get("/ops/capability-proposals", include_in_schema=False)
def ops_capability_proposals(
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_OPS_PAGE_SIZE, ge=MIN_OPS_PAGE_SIZE, le=MAX_OPS_PAGE_SIZE),
    q: str | None = Query(default=None, min_length=1, max_length=128),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    requested_by: str | None = Query(default=None, min_length=1, max_length=128),
    source_channel: str | None = Query(default=None, min_length=1, max_length=64),
    approval_level: str | None = Query(default=None, min_length=1, max_length=64),
) -> dict:
    query = session.query(CapabilityProposalModel)
    if status_filter:
        query = query.filter(CapabilityProposalModel.status == status_filter)
    if requested_by:
        query = query.filter(CapabilityProposalModel.requested_by.ilike(f"%{requested_by}%"))
    if source_channel:
        query = query.filter(CapabilityProposalModel.source_channel.ilike(f"%{source_channel}%"))
    if approval_level:
        query = query.filter(CapabilityProposalModel.likely_approval_level == approval_level)
    if q:
        query = query.filter(
            or_(
                CapabilityProposalModel.id.ilike(f"%{q}%"),
                CapabilityProposalModel.title.ilike(f"%{q}%"),
                CapabilityProposalModel.purpose.ilike(f"%{q}%"),
                CapabilityProposalModel.suggested_capability_id.ilike(f"%{q}%"),
                CapabilityProposalModel.suggested_task_type.ilike(f"%{q}%"),
                CapabilityProposalModel.original_request_preview.ilike(f"%{q}%"),
            )
        )

    total = query.count()
    proposals = (
        query.order_by(CapabilityProposalModel.created_at.desc(), CapabilityProposalModel.id.asc())
        .offset(_pagination_offset(page, page_size))
        .limit(page_size)
        .all()
    )
    return {
        "generated_at": utcnow(),
        "page": page,
        "page_size": page_size,
        "limit": page_size,
        "filters": {
            "q": q,
            "status": status_filter,
            "requested_by": requested_by,
            "source_channel": source_channel,
            "approval_level": approval_level,
        },
        "counts": {"matched": total, "returned": len(proposals)},
        "pagination": _pagination(page, page_size, total, len(proposals)),
        "proposals": [_capability_proposal_summary(session, proposal) for proposal in proposals],
    }


@router.get("/ops/capability-proposals/{proposal_id}", include_in_schema=False)
def ops_capability_proposal_detail(
    proposal_id: str,
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(CapabilityProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability proposal not found")
    return _capability_proposal_summary(session, proposal)


@router.post("/ops/capability-proposals/{proposal_id}/{decision}", include_in_schema=False)
def ops_decide_capability_proposal(
    proposal_id: str,
    decision: Literal["accept", "reject", "close", "plan", "implemented", "supersede"],
    payload: OpsCapabilityProposalDecision | None = None,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_capability_proposal_action_header),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(CapabilityProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability proposal not found")
    reason = payload.reason if payload else ""
    action_status = {
        "accept": "accepted",
        "reject": "rejected",
        "close": "closed",
        "plan": "implementation_planned",
        "implemented": "implemented",
        "supersede": "superseded",
    }[decision]

    try:
        if decision in {"accept", "reject", "close"}:
            if not reason:
                reason = {
                    "accept": "Accepted for implementation review from ops dashboard.",
                    "reject": "Rejected from ops dashboard.",
                    "close": "Closed from ops dashboard.",
                }[decision]
            close_capability_proposal(proposal, status=action_status, reason=reason)
        elif decision == "plan":
            create_implementation_plan(session, proposal, created_by="ops_dashboard", reason=reason)
        else:
            if not reason:
                reason = {
                    "implemented": "Marked implemented from ops dashboard.",
                    "supersede": "Superseded from ops dashboard.",
                }[decision]
            mark_implementation_plan_status(
                session,
                proposal,
                status=action_status,
                reason=reason,
            )
    except CapabilityProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(
        session,
        "ops_dashboard",
        f"capability.{action_status}",
        "capability_proposal",
        proposal.id,
        {
            "suggested_capability_id": proposal.suggested_capability_id,
            "suggested_task_type": proposal.suggested_task_type,
            "surface": "ops_ui",
            "reason": reason,
        },
    )
    session.commit()
    return _capability_proposal_summary(session, proposal)


@router.get("/ops/source-proposals", include_in_schema=False)
def ops_source_proposals(
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_OPS_PAGE_SIZE, ge=MIN_OPS_PAGE_SIZE, le=MAX_OPS_PAGE_SIZE),
    q: str | None = Query(default=None, min_length=1, max_length=128),
    source_id: str | None = Query(default=None, min_length=1, max_length=128),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    requested_by: str | None = Query(default=None, min_length=1, max_length=128),
) -> dict:
    query = session.query(SourceProposalModel)
    if status_filter:
        query = query.filter(SourceProposalModel.status == status_filter)
    if source_id:
        query = query.filter(SourceProposalModel.source_id.ilike(f"%{source_id}%"))
    if requested_by:
        query = query.filter(SourceProposalModel.requested_by.ilike(f"%{requested_by}%"))
    if q:
        query = query.filter(
            or_(
                SourceProposalModel.id.ilike(f"%{q}%"),
                SourceProposalModel.source_id.ilike(f"%{q}%"),
                SourceProposalModel.requested_by.ilike(f"%{q}%"),
                SourceProposalModel.summary.ilike(f"%{q}%"),
            )
        )

    total = query.count()
    proposals = (
        query.order_by(SourceProposalModel.created_at.desc(), SourceProposalModel.id.asc())
        .offset(_pagination_offset(page, page_size))
        .limit(page_size)
        .all()
    )
    return {
        "generated_at": utcnow(),
        "page": page,
        "page_size": page_size,
        "limit": page_size,
        "filters": {
            "q": q,
            "source_id": source_id,
            "status": status_filter,
            "requested_by": requested_by,
        },
        "counts": {"matched": total, "returned": len(proposals)},
        "pagination": _pagination(page, page_size, total, len(proposals)),
        "proposals": [_source_proposal_summary(proposal) for proposal in proposals],
    }


@router.get("/ops/source-proposals/{proposal_id}", include_in_schema=False)
def ops_source_proposal_detail(
    proposal_id: str,
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(SourceProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source proposal not found")
    return _source_proposal_summary(proposal)


@router.post("/ops/source-proposals/{proposal_id}/{decision}", include_in_schema=False)
def ops_decide_source_proposal(
    proposal_id: str,
    decision: Literal["approve", "reject", "apply"],
    payload: OpsSourceProposalDecision | None = None,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_source_proposal_action_header),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(SourceProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source proposal not found")
    reason = payload.reason if payload else ""
    try:
        if decision == "approve":
            approve_source_proposal_from_ops(proposal)
            action = "source.approve"
            response: dict[str, Any] = _source_proposal_summary(proposal)
        elif decision == "reject":
            reject_source_proposal(proposal)
            action = "source.reject"
            response = _source_proposal_summary(proposal)
        else:
            apply_result = apply_source_proposal(proposal)
            action = "source.apply"
            response = {
                "proposal": _source_proposal_summary(proposal),
                "apply": _bounded_value(redact_secrets(apply_result)),
            }
    except SourceProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    audit_event(
        session,
        "ops_dashboard",
        action,
        "source_proposal",
        proposal.id,
        {"source_id": proposal.source_id, "surface": "ops_ui", "reason": reason},
    )
    session.commit()
    return response


@router.get("/ops/task-change-proposals", include_in_schema=False)
def ops_task_change_proposals(
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_OPS_PAGE_SIZE, ge=MIN_OPS_PAGE_SIZE, le=MAX_OPS_PAGE_SIZE),
    q: str | None = Query(default=None, min_length=1, max_length=128),
    task_id: str | None = Query(default=None, min_length=1, max_length=128),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    approval_level: str | None = Query(default=None, min_length=1, max_length=64),
    requested_by: str | None = Query(default=None, min_length=1, max_length=128),
    risk_severity: str | None = Query(default=None, alias="risk", min_length=1, max_length=64),
) -> dict:
    query = session.query(TaskChangeProposalModel)
    if task_id:
        query = query.filter(TaskChangeProposalModel.task_id.ilike(f"%{task_id}%"))
    if status_filter:
        query = query.filter(TaskChangeProposalModel.status == status_filter)
    if approval_level:
        query = query.filter(TaskChangeProposalModel.approval_level == approval_level)
    if requested_by:
        query = query.filter(TaskChangeProposalModel.requested_by.ilike(f"%{requested_by}%"))
    if q:
        query = query.filter(
            or_(
                TaskChangeProposalModel.id.ilike(f"%{q}%"),
                TaskChangeProposalModel.task_id.ilike(f"%{q}%"),
                TaskChangeProposalModel.requested_by.ilike(f"%{q}%"),
                TaskChangeProposalModel.approval_level.ilike(f"%{q}%"),
                TaskChangeProposalModel.summary.ilike(f"%{q}%"),
            )
        )

    matched = query.order_by(TaskChangeProposalModel.created_at.desc()).all()
    if risk_severity:
        matched = [
            proposal
            for proposal in matched
            if isinstance(proposal.risk, dict) and proposal.risk.get("severity") == risk_severity
        ]
    offset = _pagination_offset(page, page_size)
    selected = matched[offset : offset + page_size]
    return {
        "generated_at": utcnow(),
        "page": page,
        "page_size": page_size,
        "limit": page_size,
        "filters": {
            "q": q,
            "task_id": task_id,
            "status": status_filter,
            "approval_level": approval_level,
            "requested_by": requested_by,
            "risk": risk_severity,
        },
        "counts": {"matched": len(matched), "returned": len(selected)},
        "pagination": _pagination(page, page_size, len(matched), len(selected)),
        "proposals": [_task_change_proposal_summary(proposal, include_configs=False) for proposal in selected],
    }


@router.get("/ops/task-change-proposals/{proposal_id}", include_in_schema=False)
def ops_task_change_proposal_detail(
    proposal_id: str,
    _: None = Depends(require_ops_access),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(TaskChangeProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task change proposal not found")
    return _task_change_proposal_summary(proposal, include_configs=True)


@router.post("/ops/task-change-proposals/{proposal_id}/approve", include_in_schema=False)
def ops_approve_task_change_proposal(
    proposal_id: str,
    payload: OpsApprovalDecision,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_task_change_action_header),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(TaskChangeProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task change proposal not found")
    try:
        approve_task_change_proposal(proposal, payload.nonce)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TaskChangeProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(
        session,
        "ops_dashboard",
        "task_change.approve",
        "task_change_proposal",
        proposal.id,
        {"task_id": proposal.task_id, "surface": "ops_ui"},
    )
    session.commit()
    return _task_change_proposal_summary(proposal, include_configs=True)


@router.post("/ops/task-change-proposals/{proposal_id}/reject", include_in_schema=False)
def ops_reject_task_change_proposal(
    proposal_id: str,
    payload: OpsApprovalRejection | None = None,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_task_change_action_header),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(TaskChangeProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task change proposal not found")
    try:
        reject_task_change_proposal(proposal)
    except TaskChangeProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(
        session,
        "ops_dashboard",
        "task_change.reject",
        "task_change_proposal",
        proposal.id,
        {"task_id": proposal.task_id, "surface": "ops_ui", "reason": payload.reason if payload else ""},
    )
    session.commit()
    return _task_change_proposal_summary(proposal, include_configs=True)


@router.post("/ops/task-change-proposals/{proposal_id}/apply", include_in_schema=False)
def ops_apply_task_change_proposal(
    proposal_id: str,
    _: None = Depends(require_ops_access),
    __: None = Depends(require_ops_task_change_action_header),
    session: Session = Depends(get_session),
) -> dict:
    proposal = session.get(TaskChangeProposalModel, proposal_id)
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task change proposal not found")
    try:
        task = apply_task_change_proposal(session, proposal, actor_role="ops_dashboard")
    except TaskChangeProposalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(
        session,
        "ops_dashboard",
        "task_change.apply",
        "task_change_proposal",
        proposal.id,
        {"task_id": proposal.task_id, "surface": "ops_ui"},
    )
    session.commit()
    return {
        "proposal": _task_change_proposal_summary(proposal, include_configs=True),
        "task": _task_summary(task, None),
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


def _pagination_offset(page: int, page_size: int) -> int:
    return (page - 1) * page_size


def _pagination(page: int, page_size: int, total: int, returned: int) -> dict:
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "returned": returned,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "min_page_size": MIN_OPS_PAGE_SIZE,
        "max_page_size": MAX_OPS_PAGE_SIZE,
    }


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


def _task_change_proposal_summary(proposal: TaskChangeProposalModel, *, include_configs: bool = False) -> dict:
    payload = proposal_to_dict(proposal, include_configs=include_configs)
    payload["risk"] = _bounded_value(redact_secrets(payload.get("risk") or {}))
    payload["diff"] = _bounded_value(redact_secrets(payload.get("diff") or {}))
    if include_configs:
        payload["base_config"] = _bounded_value(redact_secrets(payload.get("base_config") or {}))
        payload["proposed_config"] = _bounded_value(redact_secrets(payload.get("proposed_config") or {}))
    return payload


def _capability_proposal_summary(session: Session, proposal: CapabilityProposalModel) -> dict:
    payload = capability_proposal_to_dict(
        proposal,
        implementation_plan=implementation_plan_for_proposal(session, proposal.id),
    )
    return _bounded_value(
        redact_secrets(payload),
        max_depth=MAX_AUDIT_DETAIL_DEPTH,
        max_keys=MAX_AUDIT_DETAIL_KEYS,
        text_limit=MAX_AUDIT_DETAIL_TEXT,
        field_text_limit=MAX_AUDIT_DETAIL_TEXT,
    )


def _source_proposal_summary(proposal: SourceProposalModel) -> dict:
    payload = source_proposal_to_dict(proposal)
    payload["execution"] = {
        "creates_task": False,
        "creates_approval": False,
        "can_be_applied": proposal.status == "approved",
        "registry_mutation": "operator_review_only",
    }
    return _bounded_value(
        redact_secrets(payload),
        max_depth=MAX_AUDIT_DETAIL_DEPTH,
        max_keys=MAX_AUDIT_DETAIL_KEYS,
        text_limit=MAX_AUDIT_DETAIL_TEXT,
        field_text_limit=MAX_AUDIT_DETAIL_TEXT,
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
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    notification = log.get("notification") if isinstance(log.get("notification"), dict) else {}
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "result_status": result.get("status"),
        "quality_status": quality.get("status"),
        "failed_count": result.get("failed_count"),
        "notify": result.get("notify"),
        "notification": {
            "sent": notification.get("sent") if notification else None,
            "dry_run": notification.get("dry_run") if notification else None,
            "target": notification.get("target") if notification else None,
            "transport": notification.get("transport") if notification else None,
        },
    }


def _run_notification_sent(run: RunModel) -> bool | None:
    log = run.log if isinstance(run.log, dict) else {}
    notification = log.get("notification") if isinstance(log.get("notification"), dict) else {}
    sent = notification.get("sent")
    return sent if isinstance(sent, bool) else None


def _run_result(run: RunModel) -> dict:
    log = run.log if isinstance(run.log, dict) else {}
    return log.get("result") if isinstance(log.get("result"), dict) else {}


def _run_failed(run: RunModel) -> bool:
    result = _run_result(run)
    result_status = str(result.get("status") or "").lower()
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    if str(run.status or "").lower().startswith("failed"):
        return True
    if quality.get("status") in {"degraded", "failed"}:
        return True
    if result_status in {"failed", "failure", "error", "degraded"}:
        return True
    try:
        if int(result.get("failed_count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        return True
    return bool(result.get("error") or (result.get("errors") and not result.get("items")))


def _run_collection_summary(runs: list[RunModel]) -> dict:
    last_failure_at = None
    success_count = 0
    failure_count = 0
    dry_run_count = 0
    sent_discord_count = 0
    for run in runs:
        failed = _run_failed(run)
        if failed:
            failure_count += 1
            failure_at = run.completed_at or run.created_at
            if failure_at and (last_failure_at is None or failure_at > last_failure_at):
                last_failure_at = failure_at
        elif str(run.status or "").startswith("completed"):
            success_count += 1
        if "dry_run" in str(run.status or ""):
            dry_run_count += 1
        if _run_notification_sent(run) is True:
            sent_discord_count += 1
    return {
        "total": len(runs),
        "success_count": success_count,
        "failure_count": failure_count,
        "dry_run_count": dry_run_count,
        "sent_discord_count": sent_discord_count,
        "last_failure_at": last_failure_at,
    }


def _run_detail(run: RunModel, task: TaskModel | None) -> dict:
    log = _as_dict(_bounded_value(redact_secrets(run.log if isinstance(run.log, dict) else {}), max_depth=MAX_RUN_DETAIL_DEPTH + 2))
    result = _as_dict(log.get("result"))
    notification = log.get("notification") if isinstance(log.get("notification"), dict) else None
    notification_decision = (
        log.get("notification_decision") if isinstance(log.get("notification_decision"), dict) else {}
    )
    quality_alert = log.get("quality_alert") if isinstance(log.get("quality_alert"), dict) else None
    return {
        "run": _run_summary(run),
        "task": _task_summary(task, run) if task else {"id": run.task_id},
        "digest": _digest_detail(result),
        "n8n": _n8n_detail(result),
        "notification_decision": notification_decision,
        "notification": notification,
        "quality_alert": quality_alert,
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
        "approved_source_count": result.get("approved_source_count"),
        "item_count": len(items),
        "error_count": len(errors),
        "quality": _digest_quality_detail(_as_dict(result.get("quality"))),
        "source_health": [_source_health_detail(health) for health in _as_list(result.get("source_health"))[:MAX_RUN_DETAIL_ERRORS] if isinstance(health, dict)],
        "items": [_digest_item_detail(item) for item in items[:MAX_RUN_DETAIL_ITEMS] if isinstance(item, dict)],
        "errors": [_source_error_detail(error) for error in errors[:MAX_RUN_DETAIL_ERRORS] if isinstance(error, dict)],
    }


def _digest_quality_detail(quality: dict) -> dict | None:
    if not quality:
        return None
    return {
        "enabled": quality.get("enabled"),
        "status": quality.get("status"),
        "alert_needed": quality.get("alert_needed"),
        "alert_target": quality.get("alert_target"),
        "metrics": _bounded_value(quality.get("metrics")),
        "thresholds": _bounded_value(quality.get("thresholds")),
        "reasons": _bounded_value(quality.get("reasons")),
    }


def _source_health_detail(health: dict) -> dict:
    return {
        "source": _truncate_text(health.get("source"), MAX_RUN_DETAIL_FIELD_TEXT),
        "source_id": _truncate_text(health.get("source_id"), MAX_RUN_DETAIL_FIELD_TEXT),
        "status": _truncate_text(health.get("status"), 100),
        "item_count": health.get("item_count"),
        "trust_level": _truncate_text(health.get("trust_level"), 100),
        "ingestion_mode": _truncate_text(health.get("ingestion_mode"), 100),
        "error": _truncate_text(health.get("error"), 200),
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


def _approval_is_config_proposal(session: Session, approval: ApprovalModel) -> bool:
    return _approval_change_type(session, approval) in PROPOSAL_CHANGE_TYPES


def _approval_change_type(session: Session, approval: ApprovalModel) -> str | None:
    version = task_config_version_for_approval(session, approval.id)
    return version.change_type if version else None


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
    .header-actions {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .saved-view {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }}
    .saved-view select {{ min-width: 220px; }}
    .filter-bar {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin: 10px 0;
    }}
    .filter-bar input {{ width: min(320px, 100%); }}
    .filter-bar button {{ padding: 8px 10px; }}
    .page-size {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }}
    .page-size input {{
      width: 78px;
      min-width: 78px;
    }}
    .pager {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    .pager-actions {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .pager-actions button {{ padding: 6px 9px; }}
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
    th .sort-button {{
      color: var(--muted);
      font: inherit;
      font-weight: 650;
      padding: 0;
      border: 0;
      background: transparent;
      text-align: left;
      cursor: pointer;
    }}
    th .sort-button:hover {{ color: var(--accent); border: 0; }}
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
    .timeline {{
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }}
    .timeline-item {{
      display: grid;
      grid-template-columns: minmax(150px, 210px) 1fr;
      gap: 12px;
      align-items: start;
      border-left: 3px solid var(--line);
      padding: 4px 0 4px 12px;
    }}
    .timeline-item.ok {{ border-left-color: var(--ok); }}
    .timeline-item.warn {{ border-left-color: var(--warn); }}
    .timeline-item.bad {{ border-left-color: var(--bad); }}
    .timeline-time {{ color: var(--muted); font-size: 12px; }}
    .timeline-main {{ min-width: 0; }}
    .timeline-main .meta {{ margin-top: 3px; }}
    .summary-strip {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 10px;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 10px 0;
      margin-top: 12px;
    }}
    .summary-stat {{ min-width: 0; }}
    .summary-stat strong {{ display: block; font-size: 18px; margin-top: 3px; overflow-wrap: anywhere; }}
    @media (max-width: 860px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .detail-grid {{ grid-template-columns: 1fr; }}
      .timeline-item {{ grid-template-columns: 1fr; }}
      .summary-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
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
    <div class="header-actions">
      <label class="saved-view" for="saved-view-select">Saved view
        <select id="saved-view-select" aria-label="Saved dashboard view">
          <option value="">Custom</option>
          <option value="failed_runs">Failed runs</option>
          <option value="pending_approvals">Pending approvals</option>
          <option value="pending_proposals">Pending proposals</option>
          <option value="pending_capabilities">Pending capability proposals</option>
          <option value="recent_discord_sends">Recent Discord sends</option>
          <option value="task_changes">Task changes</option>
          <option value="worker_activity">Worker activity</option>
        </select>
      </label>
      <button id="refresh" type="button" title="Refresh status">Refresh</button>
    </div>
  </header>
  <nav class="tabs" aria-label="Operations views">
    <button class="tab-button active" type="button" data-view-target="overview">Overview</button>
    <button class="tab-button" type="button" data-view-target="tasks">Tasks <span class="tab-count" data-count="tasks"></span></button>
    <button class="tab-button" type="button" data-view-target="runs">Runs <span class="tab-count" data-count="runs"></span></button>
    <button class="tab-button" type="button" data-view-target="proposals">Proposals <span class="tab-count" data-count="proposals"></span></button>
    <button class="tab-button" type="button" data-view-target="capabilities">Capabilities <span class="tab-count" data-count="capabilities"></span></button>
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
          <label class="page-size" for="task-page-size">Per page
            <input id="task-page-size" type="number" min="5" max="100" step="5" value="10" aria-label="Tasks per page">
          </label>
          <button id="task-filter-clear" type="button">Clear</button>
        </div>
        <div class="table-wrap"><table id="tasks"></table></div>
        <div class="pager" id="task-pagination"></div>
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
          <button id="run-refresh" type="button">Refresh Runs</button>
        </div>
        <div class="filter-bar" aria-label="Run filters">
          <input id="run-filter-text" type="search" placeholder="Filter runs" aria-label="Filter runs">
          <input id="run-filter-task-id" type="search" placeholder="Task id" aria-label="Run task id">
          <select id="run-filter-status" aria-label="Run status">
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="dry_run">Dry-run</option>
          </select>
          <select id="run-filter-notification-sent" aria-label="Run notification result">
            <option value="">All notifications</option>
            <option value="true">Sent notifications</option>
            <option value="false">Unsent notifications</option>
          </select>
          <label class="page-size" for="run-page-size">Per page
            <input id="run-page-size" type="number" min="5" max="100" step="5" value="10" aria-label="Runs per page">
          </label>
          <button id="run-filter-clear" type="button">Clear</button>
        </div>
        <div class="table-wrap"><table id="runs"></table></div>
        <div class="pager" id="run-pagination"></div>
      </section>
      <section class="section panel" id="run-timeline">
        <h2>Run Timeline</h2>
        <div class="empty">Load a run view to see the timeline.</div>
      </section>
      <section class="section panel" id="run-detail">
        <h2>Run Detail</h2>
        <div class="empty">Select a recent run to inspect its digest, n8n response, notification decision, and Discord result.</div>
      </section>
    </section>
    <section class="view" data-view="approvals">
      <section class="section panel">
        <div class="section-head">
          <div>
            <h2>General Approvals</h2>
            <div class="meta" id="approval-filter-summary">Not loaded yet.</div>
          </div>
          <button id="approval-refresh" type="button">Refresh Approvals</button>
        </div>
        <div class="filter-bar" aria-label="Approval filters">
          <input id="approval-filter-q" type="search" placeholder="Search approvals" aria-label="Search approvals">
          <input id="approval-filter-task-id" type="search" placeholder="Task id" aria-label="Approval task id">
          <input id="approval-filter-requested-by" type="search" placeholder="Requested by" aria-label="Approval requested by">
          <select id="approval-filter-level" aria-label="Approval level">
            <option value="">All levels</option>
            <option value="L0_READ_ONLY">L0_READ_ONLY</option>
            <option value="L1_NOTIFY_ONLY">L1_NOTIFY_ONLY</option>
            <option value="L2_LOCAL_WRITE">L2_LOCAL_WRITE</option>
            <option value="L3_EXTERNAL_SIDE_EFFECT">L3_EXTERNAL_SIDE_EFFECT</option>
            <option value="L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE">L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE</option>
          </select>
          <label class="page-size" for="approval-page-size">Per page
            <input id="approval-page-size" type="number" min="5" max="100" step="5" value="10" aria-label="Approvals per page">
          </label>
          <button id="approval-filter-clear" type="button">Clear</button>
        </div>
        <div id="approvals"></div>
        <div class="pager" id="approval-pagination"></div>
      </section>
    </section>
    <section class="view" data-view="proposals">
      <section class="section panel">
        <div class="section-head">
          <div>
            <h2>Task Change Proposals</h2>
            <div class="meta" id="proposal-filter-summary">Not loaded yet.</div>
          </div>
          <button id="proposal-refresh" type="button">Refresh Proposals</button>
        </div>
        <div class="filter-bar" aria-label="Proposal filters">
          <input id="proposal-filter-q" type="search" placeholder="Search proposals" aria-label="Search proposals">
          <input id="proposal-filter-task-id" type="search" placeholder="Task id" aria-label="Proposal task id">
          <input id="proposal-filter-requested-by" type="search" placeholder="Requested by" aria-label="Proposal requested by">
          <select id="proposal-filter-level" aria-label="Proposal approval level">
            <option value="">All levels</option>
            <option value="L0_READ_ONLY">L0_READ_ONLY</option>
            <option value="L1_NOTIFY_ONLY">L1_NOTIFY_ONLY</option>
            <option value="L2_LOCAL_WRITE">L2_LOCAL_WRITE</option>
            <option value="L3_EXTERNAL_SIDE_EFFECT">L3_EXTERNAL_SIDE_EFFECT</option>
            <option value="L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE">L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE</option>
          </select>
          <select id="proposal-filter-status" aria-label="Proposal status">
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="applied">Applied</option>
            <option value="rejected">Rejected</option>
            <option value="">All statuses</option>
          </select>
          <select id="proposal-filter-risk" aria-label="Proposal risk">
            <option value="">All risk levels</option>
            <option value="standard_review">standard_review</option>
            <option value="operator_review">operator_review</option>
            <option value="admin_required">admin_required</option>
            <option value="manual_only">manual_only</option>
          </select>
          <label class="page-size" for="proposal-page-size">Per page
            <input id="proposal-page-size" type="number" min="5" max="100" step="5" value="10" aria-label="Proposals per page">
          </label>
          <button id="proposal-filter-clear" type="button">Clear</button>
        </div>
        <div id="proposals"></div>
        <div class="pager" id="proposal-pagination"></div>
      </section>
    </section>
    <section class="view" data-view="capabilities">
      <section class="section panel">
        <div class="section-head">
          <div>
            <h2>Capability Proposals</h2>
            <div class="meta" id="capability-filter-summary">Not loaded yet.</div>
          </div>
          <button id="capability-refresh" type="button">Refresh Capabilities</button>
        </div>
        <div class="filter-bar" aria-label="Capability proposal filters">
          <input id="capability-filter-q" type="search" placeholder="Search capability proposals" aria-label="Search capability proposals">
          <input id="capability-filter-requested-by" type="search" placeholder="Requested by" aria-label="Capability proposal requested by">
          <input id="capability-filter-channel" type="search" placeholder="Channel" aria-label="Capability proposal source channel">
          <select id="capability-filter-level" aria-label="Capability proposal approval level">
            <option value="">All levels</option>
            <option value="L0_READ_ONLY">L0_READ_ONLY</option>
            <option value="L1_NOTIFY_ONLY">L1_NOTIFY_ONLY</option>
            <option value="L2_LOCAL_WRITE">L2_LOCAL_WRITE</option>
            <option value="L3_EXTERNAL_SIDE_EFFECT">L3_EXTERNAL_SIDE_EFFECT</option>
            <option value="L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE">L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE</option>
          </select>
          <select id="capability-filter-status" aria-label="Capability proposal status">
            <option value="pending">Pending</option>
            <option value="accepted">Accepted</option>
            <option value="implementation_planned">Implementation planned</option>
            <option value="implemented">Implemented</option>
            <option value="superseded">Superseded</option>
            <option value="rejected">Rejected</option>
            <option value="closed">Closed</option>
            <option value="">All statuses</option>
          </select>
          <label class="page-size" for="capability-page-size">Per page
            <input id="capability-page-size" type="number" min="5" max="100" step="5" value="10" aria-label="Capability proposals per page">
          </label>
          <button id="capability-filter-clear" type="button">Clear</button>
        </div>
        <div id="capabilities"></div>
        <div class="pager" id="capability-pagination"></div>
      </section>
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
            <option value="channel_bridge">channel_bridge</option>
          </select>
          <select id="audit-filter-action" aria-label="Audit action">
            <option value="">All actions</option>
            <option value="channel.discord.replied">channel.discord.replied</option>
            <option value="channel.discord.forwarded">channel.discord.forwarded</option>
            <option value="channel.discord.blocked">channel.discord.blocked</option>
            <option value="channel.discord.rejected">channel.discord.rejected</option>
            <option value="channel.discord.failed">channel.discord.failed</option>
            <option value="research.query">research.query</option>
            <option value="research.topic_digest_suggest">research.topic_digest_suggest</option>
            <option value="approval.approve">approval.approve</option>
            <option value="approval.reject">approval.reject</option>
            <option value="approval.request">approval.request</option>
            <option value="task.draft">task.draft</option>
            <option value="task.config.revert">task.config.revert</option>
            <option value="task.update">task.update</option>
            <option value="task.pause">task.pause</option>
            <option value="task.resume">task.resume</option>
            <option value="task.run">task.run</option>
            <option value="task_change.propose">task_change.propose</option>
            <option value="task_change.approve">task_change.approve</option>
            <option value="task_change.reject">task_change.reject</option>
            <option value="task_change.apply">task_change.apply</option>
            <option value="capability.propose">capability.propose</option>
            <option value="capability.accepted">capability.accepted</option>
            <option value="capability.rejected">capability.rejected</option>
            <option value="capability.closed">capability.closed</option>
            <option value="run.claim">run.claim</option>
            <option value="run.update">run.update</option>
            <option value="maintenance.retention.preview">maintenance.retention.preview</option>
            <option value="maintenance.retention.apply">maintenance.retention.apply</option>
            <option value="heartbeat.update">heartbeat.update</option>
          </select>
          <select id="audit-filter-resource-type" aria-label="Audit resource type">
            <option value="">All resources</option>
            <option value="task">task</option>
            <option value="task_change_proposal">task_change_proposal</option>
            <option value="capability_proposal">capability_proposal</option>
            <option value="run">run</option>
            <option value="approval">approval</option>
            <option value="service">service</option>
            <option value="topic">topic</option>
            <option value="maintenance">maintenance</option>
            <option value="channel_event">channel_event</option>
            <option value="research">research</option>
          </select>
          <label class="page-size" for="audit-page-size">Per page
            <input id="audit-page-size" type="number" min="5" max="100" step="5" value="10" aria-label="Audit events per page">
          </label>
          <button id="audit-filter-clear" type="button">Clear</button>
        </div>
        <div class="table-wrap"><table id="audit"></table></div>
        <div class="pager" id="audit-pagination"></div>
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
    const MIN_PAGE_SIZE = 5;
    const DEFAULT_PAGE_SIZE = 10;
    const MAX_PAGE_SIZE = 100;
    const storageKey = key => `yggy.ops.${{key}}`;
    function storedValue(key) {{
      try {{ return window.localStorage.getItem(storageKey(key)); }}
      catch (error) {{ return null; }}
    }}
    function storeValue(key, value) {{
      try {{ window.localStorage.setItem(storageKey(key), value); }}
      catch (error) {{}}
    }}
    function boundedNumber(value, fallback, min = MIN_PAGE_SIZE, max = MAX_PAGE_SIZE) {{
      const number = Number.parseInt(value, 10);
      if (Number.isNaN(number)) return fallback;
      return Math.min(max, Math.max(min, number));
    }}
    const pageState = {{
      tasks: {{page: 1, pageSize: boundedNumber(storedValue('pageSize.tasks'), DEFAULT_PAGE_SIZE)}},
      runs: {{page: 1, pageSize: boundedNumber(storedValue('pageSize.runs'), DEFAULT_PAGE_SIZE)}},
      proposals: {{page: 1, pageSize: boundedNumber(storedValue('pageSize.proposals'), DEFAULT_PAGE_SIZE)}},
      capabilities: {{page: 1, pageSize: boundedNumber(storedValue('pageSize.capabilities'), DEFAULT_PAGE_SIZE)}},
      approvals: {{page: 1, pageSize: boundedNumber(storedValue('pageSize.approvals'), DEFAULT_PAGE_SIZE)}},
      audit: {{page: 1, pageSize: boundedNumber(storedValue('pageSize.audit'), DEFAULT_PAGE_SIZE)}},
    }};
    const allowedSorts = {{
      tasks: ['id', 'type', 'status', 'trigger', 'output', 'latest_run_completed'],
      runs: ['id', 'task_id', 'status', 'created_at', 'completed_at'],
      audit: ['created_at', 'actor_role', 'action', 'resource_type', 'resource_id'],
    }};
    function storedSort(view, fallbackBy, fallbackDir) {{
      const by = storedValue(`sortBy.${{view}}`);
      const dir = storedValue(`sortDir.${{view}}`);
      return {{
        by: allowedSorts[view].includes(by) ? by : fallbackBy,
        dir: dir === 'asc' || dir === 'desc' ? dir : fallbackDir,
      }};
    }}
    const sortState = {{
      tasks: storedSort('tasks', 'id', 'asc'),
      runs: storedSort('runs', 'created_at', 'desc'),
      audit: storedSort('audit', 'created_at', 'desc'),
    }};
    const filterFieldsByView = {{
      tasks: ['task-filter-text', 'task-filter-state', 'task-filter-type'],
      runs: ['run-filter-text', 'run-filter-task-id', 'run-filter-status', 'run-filter-notification-sent'],
      proposals: ['proposal-filter-q', 'proposal-filter-task-id', 'proposal-filter-requested-by', 'proposal-filter-level', 'proposal-filter-status', 'proposal-filter-risk'],
      capabilities: ['capability-filter-q', 'capability-filter-requested-by', 'capability-filter-channel', 'capability-filter-level', 'capability-filter-status'],
      approvals: ['approval-filter-q', 'approval-filter-task-id', 'approval-filter-requested-by', 'approval-filter-level'],
      audit: ['audit-filter-q', 'audit-filter-resource-id', 'audit-filter-actor', 'audit-filter-action', 'audit-filter-resource-type'],
    }};
    const savedViews = {{
      failed_runs: {{
        view: 'runs',
        fields: {{'run-filter-status': 'failed'}},
        sort: {{view: 'runs', by: 'created_at', dir: 'desc'}},
      }},
      pending_approvals: {{
        view: 'approvals',
        fields: {{}},
      }},
      pending_proposals: {{
        view: 'proposals',
        fields: {{}},
      }},
      pending_capabilities: {{
        view: 'capabilities',
        fields: {{'capability-filter-status': 'pending'}},
      }},
      recent_discord_sends: {{
        view: 'runs',
        fields: {{'run-filter-notification-sent': 'true'}},
        sort: {{view: 'runs', by: 'created_at', dir: 'desc'}},
      }},
      task_changes: {{
        view: 'audit',
        fields: {{'audit-filter-action': 'task_change.propose', 'audit-filter-resource-type': 'task_change_proposal'}},
        sort: {{view: 'audit', by: 'created_at', dir: 'desc'}},
      }},
      worker_activity: {{
        view: 'audit',
        fields: {{'audit-filter-actor': 'worker'}},
        sort: {{view: 'audit', by: 'created_at', dir: 'desc'}},
      }},
    }};
    function markCustomView() {{
      const select = byId('saved-view-select');
      if (select) select.value = '';
    }}
    function setField(id, value) {{
      if (!byId(id)) return;
      byId(id).value = value || '';
      persistField(id);
    }}
    function clearViewFilters(view) {{
      (filterFieldsByView[view] || []).forEach(id => setField(id, ''));
    }}
    function setSort(view, by, dir) {{
      if (!allowedSorts[view] || !allowedSorts[view].includes(by)) return;
      sortState[view].by = by;
      sortState[view].dir = dir === 'asc' ? 'asc' : 'desc';
      storeValue(`sortBy.${{view}}`, sortState[view].by);
      storeValue(`sortDir.${{view}}`, sortState[view].dir);
    }}
    function applySavedView(name) {{
      const preset = savedViews[name];
      if (!preset) return;
      clearViewFilters(preset.view);
      Object.entries(preset.fields || {{}}).forEach(([id, value]) => setField(id, value));
      if (preset.sort) setSort(preset.sort.view, preset.sort.by, preset.sort.dir);
      resetPage(preset.view);
      showView(preset.view);
      if (preset.view === 'tasks') renderTasks();
    }}
    function sortIndicator(view, key) {{
      if (sortState[view].by !== key) return '';
      return sortState[view].dir === 'asc' ? ' ↑' : ' ↓';
    }}
    function sortHeader(view, label, key) {{
      return `<button type="button" class="sort-button" data-sort-view="${{esc(view)}}" data-sort-key="${{esc(key)}}" aria-label="Sort ${{esc(view)}} by ${{esc(label)}}">${{esc(label)}}${{sortIndicator(view, key)}}</button>`;
    }}
    function updateSort(view, key, onChange) {{
      if (!allowedSorts[view].includes(key)) return;
      if (sortState[view].by === key) {{
        sortState[view].dir = sortState[view].dir === 'asc' ? 'desc' : 'asc';
      }} else {{
        sortState[view].by = key;
        sortState[view].dir = key.endsWith('_at') || key === 'latest_run_completed' ? 'desc' : 'asc';
      }}
      storeValue(`sortBy.${{view}}`, sortState[view].by);
      storeValue(`sortDir.${{view}}`, sortState[view].dir);
      markCustomView();
      resetPage(view);
      onChange();
    }}
    function wireSortHeaders(containerId, view, onChange) {{
      byId(containerId).querySelectorAll('[data-sort-key]').forEach(button => {{
        button.addEventListener('click', () => updateSort(view, button.dataset.sortKey, onChange));
      }});
    }}
    function pageSize(view) {{
      const inputId = view === 'capabilities' ? 'capability-page-size' : `${{view.slice(0, -1)}}-page-size`;
      const input = byId(inputId) || byId(`${{view}}-page-size`);
      const size = boundedNumber(input?.value, pageState[view].pageSize);
      pageState[view].pageSize = size;
      if (input) input.value = size;
      storeValue(`pageSize.${{view}}`, String(size));
      return size;
    }}
    function setPage(view, page) {{
      pageState[view].page = Math.max(1, page);
    }}
    function resetPage(view) {{
      setPage(view, 1);
    }}
    function renderPager(id, view, total, returned, onPageChange) {{
      const pager = byId(id);
      if (!pager) return;
      const state = pageState[view];
      const totalPages = Math.max(1, Math.ceil(total / state.pageSize));
      if (state.page > totalPages) state.page = totalPages;
      const from = total === 0 ? 0 : ((state.page - 1) * state.pageSize) + 1;
      const to = total === 0 ? 0 : Math.min(total, from + returned - 1);
      pager.innerHTML = `
        <div class="meta">Page ${{state.page}} of ${{totalPages}}; items ${{from}}-${{to}} of ${{total}}</div>
        <div class="pager-actions">
          <button type="button" data-page-action="previous"${{state.page <= 1 ? ' disabled' : ''}}>Previous</button>
          <button type="button" data-page-action="next"${{state.page >= totalPages ? ' disabled' : ''}}>Next</button>
        </div>`;
      pager.querySelector('[data-page-action="previous"]').addEventListener('click', () => {{
        if (state.page <= 1) return;
        state.page -= 1;
        onPageChange();
      }});
      pager.querySelector('[data-page-action="next"]').addEventListener('click', () => {{
        if (state.page >= totalPages) return;
        state.page += 1;
        onPageChange();
      }});
    }}
    function restorePersistentFields() {{
      [
        'task-filter-text', 'task-filter-state', 'task-filter-type',
        'run-filter-text', 'run-filter-task-id', 'run-filter-status', 'run-filter-notification-sent',
        'proposal-filter-q', 'proposal-filter-task-id', 'proposal-filter-requested-by', 'proposal-filter-level', 'proposal-filter-status', 'proposal-filter-risk',
        'capability-filter-q', 'capability-filter-requested-by', 'capability-filter-channel', 'capability-filter-level', 'capability-filter-status',
        'approval-filter-q', 'approval-filter-task-id', 'approval-filter-requested-by', 'approval-filter-level',
        'audit-filter-q', 'audit-filter-resource-id', 'audit-filter-actor', 'audit-filter-action', 'audit-filter-resource-type',
      ].forEach(id => {{
        const value = storedValue(`field.${{id}}`);
        if (value !== null && byId(id)) byId(id).value = value;
      }});
      Object.entries(pageState).forEach(([view, state]) => {{
        const inputId = view === 'capabilities' ? 'capability-page-size' : `${{view.slice(0, -1)}}-page-size`;
        const input = byId(inputId) || byId(`${{view}}-page-size`);
        if (input) input.value = state.pageSize;
      }});
    }}
    function persistField(id) {{
      if (byId(id)) storeValue(`field.${{id}}`, byId(id).value);
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
      if (view === 'runs') loadRuns();
      if (view === 'audit') loadAudit();
      if (view === 'proposals') loadTaskChangeProposals();
      if (view === 'capabilities') loadCapabilityProposals();
      if (view === 'approvals') loadReviewQueue('approvals');
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
    function comparable(value) {{
      if (value === null || value === undefined) return '';
      return String(value).toLowerCase();
    }}
    function compareValues(left, right, dir) {{
      const a = comparable(left);
      const b = comparable(right);
      if (a < b) return dir === 'asc' ? -1 : 1;
      if (a > b) return dir === 'asc' ? 1 : -1;
      return 0;
    }}
    function taskSortValue(task, key) {{
      if (key === 'id') return task.id;
      if (key === 'type') return task.type;
      if (key === 'status') return `${{task.status}} ${{task.enabled ? 'enabled' : 'disabled'}}`;
      if (key === 'trigger') return `${{task.trigger?.timezone || ''}} ${{task.trigger?.cron || ''}}`;
      if (key === 'output') return `${{task.output?.channel || ''}} ${{task.output?.target || ''}}`;
      if (key === 'latest_run_completed') return task.latest_run?.completed_at || task.latest_run?.created_at || '';
      return task.id;
    }}
    function sortTasks(tasks) {{
      const sort = sortState.tasks;
      return [...tasks].sort((left, right) => {{
        const primary = compareValues(taskSortValue(left, sort.by), taskSortValue(right, sort.by), sort.dir);
        return primary || compareValues(left.id, right.id, 'asc');
      }});
    }}
    function syncTaskTypeOptions(tasks) {{
      const select = byId('task-filter-type');
      const selected = select.value || storedValue('field.task-filter-type') || '';
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
    function wireTaskTimelineButtons() {{
      document.querySelectorAll('[data-task-timeline-id]').forEach(button => {{
        button.addEventListener('click', () => showTaskTimeline(button.dataset.taskTimelineId));
      }});
    }}
    function showTaskTimeline(taskId) {{
      clearViewFilters('runs');
      setField('run-filter-task-id', taskId);
      setSort('runs', 'created_at', 'desc');
      resetPage('runs');
      markCustomView();
      showView('runs');
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
      const filtered = sortTasks(tasks.filter(taskMatchesFilters));
      const state = pageState.tasks;
      const size = pageSize('tasks');
      const totalPages = Math.max(1, Math.ceil(filtered.length / size));
      if (state.page > totalPages) state.page = totalPages;
      const start = (state.page - 1) * size;
      const pageRows = filtered.slice(start, start + size);
      byId('task-filter-summary').textContent = `Showing ${{pageRows.length}} of ${{filtered.length}} matching tasks; ${{tasks.length}} total.`;
      renderTable('tasks', [
        sortHeader('tasks', 'Task', 'id'),
        sortHeader('tasks', 'Type', 'type'),
        sortHeader('tasks', 'State', 'status'),
        sortHeader('tasks', 'Trigger', 'trigger'),
        sortHeader('tasks', 'Output', 'output'),
        sortHeader('tasks', 'Latest Run', 'latest_run_completed'),
        'Actions',
      ], pageRows.map(task => [
        `${{taskButton(task)}}<br><span class="meta">${{esc(task.name)}}</span>`,
        `<span class="pill">${{esc(task.type)}}</span><br><span class="meta">${{esc(task.approval_level)}}</span>`,
        `${{statusLabel(task.enabled, task.enabled ? 'enabled' : 'disabled')}}<br><span class="meta">status ${{esc(task.status)}}; dry run ${{task.dry_run}}</span>`,
        `<code>${{esc(task.trigger.cron)}}</code><br><span class="meta">${{esc(task.trigger.timezone)}}</span>`,
        `${{esc(task.output.channel)}}<br><span class="meta">${{esc(task.output.target)}}</span>`,
        task.latest_run ? `${{runButton(task.latest_run)}} ${{statusLabel(task.latest_run.status)}}<br><span class="meta">${{esc(task.latest_run.completed_at)}}</span>` : '<span class="meta">no runs</span>',
        `${{taskRunButtons(task)}}${{taskStateButtons(task)}}`,
      ]), 'No tasks match the current filters.');
      renderPager('task-pagination', 'tasks', filtered.length, pageRows.length, renderTasks);
      wireSortHeaders('tasks', 'tasks', renderTasks);
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
            <div class="state-actions">
              <button type="button" data-task-timeline-id="${{esc(task.id)}}" title="Show filtered run timeline for this task">Timeline</button>
            </div>
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
      wireTaskTimelineButtons();
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
    function runFilterValues() {{
      return {{
        q: fieldValue('run-filter-text'),
        task_id: fieldValue('run-filter-task-id'),
        status: fieldValue('run-filter-status'),
        notification_sent: fieldValue('run-filter-notification-sent'),
      }};
    }}
    async function loadRuns() {{
      const summary = byId('run-filter-summary');
      summary.textContent = 'Loading runs...';
      try {{
        const params = new URLSearchParams({{
          page: String(pageState.runs.page),
          page_size: String(pageSize('runs')),
          sort_by: sortState.runs.by,
          sort_dir: sortState.runs.dir,
        }});
        Object.entries(runFilterValues()).forEach(([key, value]) => {{
          if (value) params.set(key, value);
        }});
        const response = await fetch(`/ops/runs?${{params.toString()}}`, {{credentials: 'same-origin'}});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const data = await response.json();
        if (data.pagination.total > 0 && pageState.runs.page > data.pagination.total_pages) {{
          pageState.runs.page = data.pagination.total_pages;
          return loadRuns();
        }}
        renderRuns(data.runs || [], data.pagination, data.summary || {{}});
      }} catch (error) {{
        summary.textContent = `Unable to load runs: ${{error.message}}`;
      }}
    }}
    function renderRuns(runs, pagination, runSummary) {{
      const total = pagination?.total ?? runs.length;
      byId('run-filter-summary').textContent = `Showing ${{runs.length}} of ${{total}} matching runs.`;
      renderTable('runs', [
        sortHeader('runs', 'Run', 'id'),
        sortHeader('runs', 'Task', 'task_id'),
        sortHeader('runs', 'Status', 'status'),
        'Result',
        'Notification',
        sortHeader('runs', 'Created', 'created_at'),
        sortHeader('runs', 'Completed', 'completed_at'),
      ], runs.map(run => [
        runButton(run),
        `<code>${{esc(run.task_id)}}</code>`,
        statusLabel(run.status),
        `${{esc(run.result_status)}}${{run.quality_status ? `<br><span class="meta">quality ${{esc(run.quality_status)}}</span>` : ''}}${{run.failed_count !== null && run.failed_count !== undefined ? `<br><span class="meta">failed checks ${{esc(run.failed_count)}}</span>` : ''}}`,
        `${{run.notification.sent === true ? 'sent' : run.notification.sent === false ? 'not sent' : 'n/a'}}<br><span class="meta">${{esc(run.notification.target || run.notification.transport)}}</span>`,
        esc(run.created_at),
        esc(run.completed_at),
      ]), 'No runs match the current filters.');
      renderPager('run-pagination', 'runs', total, runs.length, loadRuns);
      renderRunTimeline(runs, pagination, runSummary);
      wireSortHeaders('runs', 'runs', loadRuns);
      wireRunLinks();
    }}
    function runTimelineContext() {{
      const filters = runFilterValues();
      const parts = [];
      const savedSelect = byId('saved-view-select');
      if (savedSelect?.value) parts.push(`saved view ${{savedSelect.options[savedSelect.selectedIndex].text}}`);
      if (filters.task_id) parts.push(`task ${{filters.task_id}}`);
      if (filters.status) parts.push(`status ${{filters.status}}`);
      if (filters.notification_sent === 'true') parts.push('sent notifications');
      if (filters.notification_sent === 'false') parts.push('unsent notifications');
      if (filters.q) parts.push(`search "${{filters.q}}"`);
      return parts.length ? parts.join('; ') : 'all runs';
    }}
    function runNotificationLabel(run) {{
      if (run.notification?.sent === true) return `notification sent${{run.notification.target ? ` to ${{run.notification.target}}` : ''}}`;
      if (run.notification?.sent === false) return `notification not sent${{run.notification.target ? ` for ${{run.notification.target}}` : ''}}`;
      return 'notification n/a';
    }}
    function renderRunSummary(summary) {{
      const data = summary || {{}};
      return `<div class="summary-strip" id="run-summary-strip" aria-label="Run summary">
        <div class="summary-stat"><div class="meta">Total</div><strong>${{esc(data.total ?? 0)}}</strong></div>
        <div class="summary-stat"><div class="meta">Success</div><strong>${{esc(data.success_count ?? 0)}}</strong></div>
        <div class="summary-stat"><div class="meta">Failures</div><strong>${{esc(data.failure_count ?? 0)}}</strong></div>
        <div class="summary-stat"><div class="meta">Dry-runs</div><strong>${{esc(data.dry_run_count ?? 0)}}</strong></div>
        <div class="summary-stat"><div class="meta">Discord sent</div><strong>${{esc(data.sent_discord_count ?? 0)}}</strong></div>
        <div class="summary-stat"><div class="meta">Last failure</div><strong>${{esc(data.last_failure_at || 'n/a')}}</strong></div>
      </div>`;
    }}
    function renderRunTimeline(runs, pagination, runSummary) {{
      const panel = byId('run-timeline');
      const total = pagination?.total ?? runs.length;
      const context = runTimelineContext();
      if (!runs.length) {{
        panel.innerHTML = `<h2>Run Timeline</h2><div class="meta">${{esc(context)}}; 0 matching runs.</div>${{renderRunSummary(runSummary)}}<div class="empty">No runs match the current filters.</div>`;
        return;
      }}
      panel.innerHTML = `
        <div class="section-head">
          <div>
            <h2>Run Timeline</h2>
            <div class="meta">${{esc(context)}}; showing ${{runs.length}} of ${{total}} matching runs in current sort order.</div>
          </div>
        </div>
        ${{renderRunSummary(runSummary)}}
        <div class="timeline">
          ${{runs.map(run => `
            <div class="timeline-item ${{statusClass(run.status)}}">
              <div class="timeline-time">
                <div>${{esc(run.created_at)}}</div>
                <div>${{run.completed_at ? `completed ${{esc(run.completed_at)}}` : 'not completed'}}</div>
              </div>
              <div class="timeline-main">
                <div>${{runButton(run)}} <code>${{esc(run.task_id)}}</code> ${{statusLabel(run.status)}}</div>
                <div class="meta">result ${{esc(run.result_status)}}${{run.quality_status ? `; quality ${{esc(run.quality_status)}}` : ''}}; ${{esc(runNotificationLabel(run))}}${{run.failed_count !== null && run.failed_count !== undefined ? `; failed checks ${{esc(run.failed_count)}}` : ''}}</div>
              </div>
            </div>
          `).join('')}}
        </div>
      `;
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
      const qualityAlert = data.quality_alert || null;
      const failure = data.failure || null;
      document.getElementById('run-detail').innerHTML = `
        <h2>Run Detail</h2>
        <div class="meta"><code>${{esc(run.id)}}</code> for <code>${{esc(run.task_id || task.id)}}</code> - ${{statusLabel(run.status)}} - completed ${{esc(run.completed_at)}}</div>
        ${{failure ? `<div class="section bad"><strong>Failure</strong><br>${{esc(failure.error)}} ${{esc(failure.message)}}</div>` : ''}}
        <div class="detail-grid section">
          <div class="detail-block wide">
            <h3>Digest</h3>
            ${{digest ? `
              <div class="meta">status ${{esc(digest.status)}}; quality ${{esc(digest.quality?.status || 'n/a')}}; mode ${{esc(digest.summary_mode)}}; items ${{esc(digest.item_count)}}; errors ${{esc(digest.error_count)}}; sources ${{esc(digest.approved_source_count ?? 'n/a')}}/${{esc(digest.source_count)}}</div>
              ${{digest.quality ? `<h3>Quality</h3>${{jsonBlock(digest.quality)}}` : ''}}
              <pre>${{esc(digest.message || '')}}</pre>
              ${{digestItems(digest.items)}}
              ${{digest.errors && digest.errors.length ? `<h3>Source Errors</h3>${{jsonBlock(digest.errors)}}` : ''}}
              ${{digest.source_health && digest.source_health.length ? `<h3>Source Health</h3>${{jsonBlock(digest.source_health)}}` : ''}}
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
          <div class="detail-block">
            <h3>Quality Alert</h3>
            ${{qualityAlert ? jsonBlock(qualityAlert) : '<div class="empty">No quality alert recorded.</div>'}}
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
    function approvalCards(approvals, emptyText) {{
      return approvals.length ? approvals.map(item => {{
        const review = item.review || {{}};
        const task = item.task || {{}};
        const actions = review.actions || [];
        return `<div class="approval">
          <div class="approval-head">
            <div>
              <code>${{esc(item.id)}}</code> for <code>${{esc(item.task_id)}}</code>
              <span class="pill">${{esc(item.approval_level)}}</span>
              ${{review.config_diff?.change_type ? `<span class="pill">${{esc(review.config_diff.change_type)}}</span>` : ''}}
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
      }}).join('<hr>') : `<div class="empty">${{esc(emptyText)}}</div>`;
    }}
    function wireApprovalButtons(container) {{
      container.querySelectorAll('[data-approval-action]').forEach(button => {{
        button.addEventListener('click', () => decideApproval(button));
      }});
    }}
    function proposalRiskText(risk) {{
      const data = risk || {{}};
      const categories = data.categories || {{}};
      const categoryText = Object.entries(categories).map(([category, paths]) => {{
        const pathText = Array.isArray(paths) ? paths.join(', ') : text(paths);
        return `${{category}}: ${{pathText}}`;
      }}).join('; ');
      return categoryText ? `${{data.severity || 'n/a'}}; ${{categoryText}}` : (data.severity || 'n/a');
    }}
    function taskChangeProposalCards(proposals, emptyText) {{
      return proposals.length ? proposals.map(item => {{
        const risk = item.risk || {{}};
        const diff = item.diff || {{}};
        const pending = item.status === 'pending';
        const approved = item.status === 'approved';
        return `<div class="approval">
          <div class="approval-head">
            <div>
              <code>${{esc(item.id)}}</code> for <code>${{esc(item.task_id)}}</code>
              <span class="pill">${{esc(item.status)}}</span>
              <span class="pill">${{esc(item.approval_level)}}</span>
              <span class="pill">${{esc(risk.severity || 'n/a')}}</span>
            </div>
            <span class="meta">requested by ${{esc(item.requested_by)}} at ${{esc(item.created_at)}}</span>
          </div>
          <div>${{esc(item.summary)}}</div>
          <div class="meta">risk ${{esc(proposalRiskText(risk))}}; base enabled ${{esc(risk.base_enabled)}} -> proposed enabled ${{esc(risk.proposed_enabled)}}</div>
          <div><strong>Config diff</strong><br>
            <span class="meta">${{configDiffSummary(diff)}}</span>
            ${{configDiffList(diff)}}
          </div>
          <div class="approval-actions">
            ${{pending ? '<input type="password" autocomplete="off" placeholder="Proposal nonce" aria-label="Proposal nonce">' : ''}}
            ${{pending ? `<button type="button" data-proposal-action="approve" data-proposal-id="${{esc(item.id)}}">Approve</button>` : ''}}
            ${{approved ? `<button type="button" data-proposal-action="apply" data-proposal-id="${{esc(item.id)}}">Apply</button>` : ''}}
            ${{pending || approved ? `<button type="button" class="danger" data-proposal-action="reject" data-proposal-id="${{esc(item.id)}}">Reject</button>` : ''}}
            <button type="button" data-proposal-detail-id="${{esc(item.id)}}">Details</button>
            <span class="meta approval-message"></span>
          </div>
          <div class="proposal-detail-panel"></div>
        </div>`;
      }}).join('<hr>') : `<div class="empty">${{esc(emptyText)}}</div>`;
    }}
    function wireTaskChangeProposalButtons(container) {{
      container.querySelectorAll('[data-proposal-action]').forEach(button => {{
        button.addEventListener('click', () => decideTaskChangeProposal(button));
      }});
      container.querySelectorAll('[data-proposal-detail-id]').forEach(button => {{
        button.addEventListener('click', () => loadTaskChangeProposalDetail(button));
      }});
    }}
    function renderTaskChangeProposals(proposals) {{
      const container = document.getElementById('proposals');
      container.innerHTML = '<div class="meta">Task changes are proposed by Yggdrasil or the CLI, but approve/apply/reject actions stay local to this ops surface.</div>'
        + taskChangeProposalCards(proposals, 'No task change proposals match the current filters.');
      wireTaskChangeProposalButtons(container);
    }}
    function itemList(items, emptyText = 'none') {{
      return Array.isArray(items) && items.length
        ? `<ul class="diff-list">${{items.map(item => `<li>${{esc(item)}}</li>`).join('')}}</ul>`
        : `<div class="empty">${{esc(emptyText)}}</div>`;
    }}
    function implementationPlanBlock(plan) {{
      if (!plan) return '<div class="empty">No implementation plan recorded yet.</div>';
      return `<details open>
        <summary>Implementation plan <span class="pill">${{esc(plan.status)}}</span></summary>
        <div class="meta">${{esc(plan.summary)}}</div>
        <div><strong>Files to change</strong>${{itemList(plan.files_to_change)}}</div>
        <div><strong>Required decisions</strong>${{itemList(plan.required_decisions)}}</div>
        <div><strong>Security boundaries</strong>${{itemList(plan.security_boundaries)}}</div>
        <div><strong>Acceptance tests</strong>${{itemList(plan.acceptance_tests)}}</div>
      </details>`;
    }}
    function capabilityProposalCards(proposals, emptyText) {{
      return proposals.length ? proposals.map(item => {{
        const pending = item.status === 'pending';
        const accepted = item.status === 'accepted';
        const planned = item.status === 'implementation_planned';
        return `<div class="approval">
          <div class="approval-head">
            <div>
              <code>${{esc(item.id)}}</code>
              <span class="pill">${{esc(item.status)}}</span>
              <span class="pill">${{esc(item.likely_approval_level)}}</span>
              <span class="pill">${{esc(item.source_channel)}}</span>
            </div>
            <span class="meta">requested by ${{esc(item.requested_by)}} at ${{esc(item.created_at)}}</span>
          </div>
          <div><strong>${{esc(item.title)}}</strong></div>
          <div>${{esc(item.purpose)}}</div>
          <div class="meta">
            suggested capability <code>${{esc(item.suggested_capability_id)}}</code>;
            task type <code>${{esc(item.suggested_task_type)}}</code>
          </div>
          <div><strong>Required inputs</strong>${{itemList(item.required_inputs)}}</div>
          <div><strong>Safety rules</strong>${{itemList(item.safety_rules)}}</div>
          ${{item.implementation_plan ? `<div>${{implementationPlanBlock(item.implementation_plan)}}</div>` : ''}}
          <div><strong>Execution boundary</strong><br>
            <span class="meta">creates task: ${{esc(item.execution?.creates_task)}}; creates approval: ${{esc(item.execution?.creates_approval)}}; can be applied: ${{esc(item.execution?.can_be_applied)}}</span>
          </div>
          <div class="approval-actions">
            ${{pending || accepted || planned ? '<input type="text" autocomplete="off" placeholder="Review note (optional)" aria-label="Capability proposal review note">' : ''}}
            ${{pending ? `<button type="button" data-capability-action="accept" data-capability-id="${{esc(item.id)}}">Accept</button>` : ''}}
            ${{pending ? `<button type="button" class="danger" data-capability-action="reject" data-capability-id="${{esc(item.id)}}">Reject</button>` : ''}}
            ${{pending ? `<button type="button" data-capability-action="close" data-capability-id="${{esc(item.id)}}">Close</button>` : ''}}
            ${{accepted ? `<button type="button" data-capability-action="plan" data-capability-id="${{esc(item.id)}}">Plan implementation</button>` : ''}}
            ${{accepted ? `<button type="button" data-capability-action="close" data-capability-id="${{esc(item.id)}}">Close</button>` : ''}}
            ${{planned ? `<button type="button" data-capability-action="implemented" data-capability-id="${{esc(item.id)}}" title="Requires the capability to be registered first">Mark implemented</button>` : ''}}
            ${{planned ? `<button type="button" class="danger" data-capability-action="supersede" data-capability-id="${{esc(item.id)}}">Supersede</button>` : ''}}
            <button type="button" data-capability-detail-id="${{esc(item.id)}}">Details</button>
            <span class="meta approval-message"></span>
          </div>
          <div class="proposal-detail-panel"></div>
        </div>`;
      }}).join('<hr>') : `<div class="empty">${{esc(emptyText)}}</div>`;
    }}
    function wireCapabilityProposalButtons(container) {{
      container.querySelectorAll('[data-capability-action]').forEach(button => {{
        button.addEventListener('click', () => decideCapabilityProposal(button));
      }});
      container.querySelectorAll('[data-capability-detail-id]').forEach(button => {{
        button.addEventListener('click', () => loadCapabilityProposalDetail(button));
      }});
    }}
    function renderCapabilityProposals(proposals) {{
      const container = document.getElementById('capabilities');
      container.innerHTML = '<div class="meta">Capability proposals are implementation backlog only. Accepting one records operator interest; it does not create a task, approval, run, or executable Yggdrasil request.</div>'
        + capabilityProposalCards(proposals, 'No capability proposals match the current filters.');
      wireCapabilityProposalButtons(container);
    }}
    function renderApprovals(approvals) {{
      const container = document.getElementById('approvals');
      container.innerHTML = '<div class="meta">Pending approvals that are not config proposals.</div>'
        + approvalCards(approvals, 'No pending general approvals match the current filters.');
      wireApprovalButtons(container);
    }}
    function taskChangeProposalFilterValues() {{
      return {{
        q: fieldValue('proposal-filter-q'),
        task_id: fieldValue('proposal-filter-task-id'),
        requested_by: fieldValue('proposal-filter-requested-by'),
        approval_level: fieldValue('proposal-filter-level'),
        status: fieldValue('proposal-filter-status'),
        risk: fieldValue('proposal-filter-risk'),
      }};
    }}
    function capabilityProposalFilterValues() {{
      return {{
        q: fieldValue('capability-filter-q'),
        requested_by: fieldValue('capability-filter-requested-by'),
        source_channel: fieldValue('capability-filter-channel'),
        approval_level: fieldValue('capability-filter-level'),
        status: fieldValue('capability-filter-status'),
      }};
    }}
    function reviewFilterValues(kind) {{
      const prefix = kind === 'proposals' ? 'proposal' : 'approval';
      const filters = {{
        q: fieldValue(`${{prefix}}-filter-q`),
        task_id: fieldValue(`${{prefix}}-filter-task-id`),
        requested_by: fieldValue(`${{prefix}}-filter-requested-by`),
        approval_level: fieldValue(`${{prefix}}-filter-level`),
      }};
      return filters;
    }}
    async function loadTaskChangeProposals() {{
      const summary = byId('proposal-filter-summary');
      summary.textContent = 'Loading task change proposals...';
      try {{
        const params = new URLSearchParams({{
          page: String(pageState.proposals.page),
          page_size: String(pageSize('proposals')),
        }});
        Object.entries(taskChangeProposalFilterValues()).forEach(([key, value]) => {{
          if (value) params.set(key, value);
        }});
        const response = await fetch(`/ops/task-change-proposals?${{params.toString()}}`, {{credentials: 'same-origin'}});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const data = await response.json();
        if (data.pagination.total > 0 && pageState.proposals.page > data.pagination.total_pages) {{
          pageState.proposals.page = data.pagination.total_pages;
          return loadTaskChangeProposals();
        }}
        summary.textContent = `Showing ${{data.counts.returned}} of ${{data.counts.matched}} matching task change proposals.`;
        renderTaskChangeProposals(data.proposals || []);
        renderPager('proposal-pagination', 'proposals', data.pagination.total, data.pagination.returned, loadTaskChangeProposals);
      }} catch (error) {{
        summary.textContent = `Unable to load task change proposals: ${{error.message}}`;
      }}
    }}
    async function loadCapabilityProposals() {{
      const summary = byId('capability-filter-summary');
      summary.textContent = 'Loading capability proposals...';
      try {{
        const params = new URLSearchParams({{
          page: String(pageState.capabilities.page),
          page_size: String(pageSize('capabilities')),
        }});
        Object.entries(capabilityProposalFilterValues()).forEach(([key, value]) => {{
          if (value) params.set(key, value);
        }});
        const response = await fetch(`/ops/capability-proposals?${{params.toString()}}`, {{credentials: 'same-origin'}});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const data = await response.json();
        if (data.pagination.total > 0 && pageState.capabilities.page > data.pagination.total_pages) {{
          pageState.capabilities.page = data.pagination.total_pages;
          return loadCapabilityProposals();
        }}
        summary.textContent = `Showing ${{data.counts.returned}} of ${{data.counts.matched}} matching capability proposals.`;
        renderCapabilityProposals(data.proposals || []);
        renderPager('capability-pagination', 'capabilities', data.pagination.total, data.pagination.returned, loadCapabilityProposals);
      }} catch (error) {{
        summary.textContent = `Unable to load capability proposals: ${{error.message}}`;
      }}
    }}
    async function loadReviewQueue(kind) {{
      const prefix = kind === 'proposals' ? 'proposal' : 'approval';
      const summary = byId(`${{prefix}}-filter-summary`);
      summary.textContent = 'Loading reviews...';
      try {{
        const params = new URLSearchParams({{
          kind,
          page: String(pageState[kind].page),
          page_size: String(pageSize(kind)),
        }});
        Object.entries(reviewFilterValues(kind)).forEach(([key, value]) => {{
          if (value) params.set(key, value);
        }});
        const response = await fetch(`/ops/reviews?${{params.toString()}}`, {{credentials: 'same-origin'}});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const data = await response.json();
        if (data.pagination.total > 0 && pageState[kind].page > data.pagination.total_pages) {{
          pageState[kind].page = data.pagination.total_pages;
          return loadReviewQueue(kind);
        }}
        summary.textContent = `Showing ${{data.counts.returned}} of ${{data.counts.matched}} matching reviews.`;
        if (kind === 'proposals') renderTaskChangeProposals(data.reviews || []);
        else renderApprovals(data.reviews || []);
        renderPager(`${{prefix}}-pagination`, kind, data.pagination.total, data.pagination.returned, () => loadReviewQueue(kind));
      }} catch (error) {{
        summary.textContent = `Unable to load reviews: ${{error.message}}`;
      }}
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
    async function loadTaskChangeProposalDetail(button) {{
      const proposalId = button.dataset.proposalDetailId;
      const panel = button.closest('.approval').querySelector('.proposal-detail-panel');
      panel.innerHTML = '<div class="empty">Loading proposal detail...</div>';
      try {{
        const response = await fetch(`/ops/task-change-proposals/${{encodeURIComponent(proposalId)}}`, {{credentials: 'same-origin'}});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const detail = await response.json();
        panel.innerHTML = `<details open>
          <summary>Proposal detail</summary>
          <div class="detail-grid section">
            <div class="detail-block">
              <h3>Base Config</h3>
              ${{jsonBlock(detail.base_config)}}
            </div>
            <div class="detail-block">
              <h3>Proposed Config</h3>
              ${{jsonBlock(detail.proposed_config)}}
            </div>
          </div>
        </details>`;
      }} catch (error) {{
        panel.innerHTML = `<div class="bad">Unable to load proposal detail: ${{esc(error.message)}}</div>`;
      }}
    }}
    async function decideTaskChangeProposal(button) {{
      const proposalId = button.dataset.proposalId;
      const action = button.dataset.proposalAction;
      const panel = button.closest('.approval');
      const message = panel.querySelector('.approval-message');
      const input = panel.querySelector('input');
      const body = action === 'approve'
        ? {{nonce: input?.value || ''}}
        : action === 'reject'
          ? {{reason: 'Rejected from ops dashboard'}}
          : null;
      if (action === 'approve' && !input?.value) {{
        message.textContent = 'Proposal nonce is required.';
        message.className = 'meta approval-message bad';
        return;
      }}
      button.disabled = true;
      message.textContent = `${{action}} pending...`;
      message.className = 'meta approval-message';
      try {{
        const response = await fetch(`/ops/task-change-proposals/${{encodeURIComponent(proposalId)}}/${{action}}`, {{
          method: 'POST',
          credentials: 'same-origin',
          headers: body
            ? {{'Content-Type': 'application/json', 'X-Yggy-Ops-Action': 'task-change-proposal'}}
            : {{'X-Yggy-Ops-Action': 'task-change-proposal'}},
          body: body ? JSON.stringify(body) : undefined,
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
    async function loadCapabilityProposalDetail(button) {{
      const proposalId = button.dataset.capabilityDetailId;
      const panel = button.closest('.approval').querySelector('.proposal-detail-panel');
      panel.innerHTML = '<div class="empty">Loading capability proposal detail...</div>';
      try {{
        const response = await fetch(`/ops/capability-proposals/${{encodeURIComponent(proposalId)}}`, {{credentials: 'same-origin'}});
        if (!response.ok) {{
          const error = await response.json().catch(() => ({{detail: `status ${{response.status}}`}}));
          throw new Error(error.detail || `status ${{response.status}}`);
        }}
        const detail = await response.json();
        panel.innerHTML = `<details open>
          <summary>Capability proposal detail</summary>
          <div class="detail-grid section">
            <div class="detail-block">
              <h3>Original Request Preview</h3>
              <pre>${{esc(detail.original_request_preview || '')}}</pre>
            </div>
            <div class="detail-block">
              <h3>Non-goals</h3>
              ${{itemList(detail.non_goals)}}
            </div>
            <div class="detail-block">
              <h3>Review Notes</h3>
              <pre>${{esc(detail.review_notes || '')}}</pre>
            </div>
            <div class="detail-block wide">
              <h3>Implementation Plan</h3>
              ${{implementationPlanBlock(detail.implementation_plan)}}
            </div>
            <div class="detail-block wide">
              <h3>Raw Proposal</h3>
              ${{jsonBlock(detail)}}
            </div>
          </div>
        </details>`;
      }} catch (error) {{
        panel.innerHTML = `<div class="bad">Unable to load capability proposal detail: ${{esc(error.message)}}</div>`;
      }}
    }}
    async function decideCapabilityProposal(button) {{
      const proposalId = button.dataset.capabilityId;
      const action = button.dataset.capabilityAction;
      const panel = button.closest('.approval');
      const message = panel.querySelector('.approval-message');
      const input = panel.querySelector('input');
      const reason = input?.value || '';
      button.disabled = true;
      message.textContent = `${{action}} pending...`;
      message.className = 'meta approval-message';
      try {{
        const response = await fetch(`/ops/capability-proposals/${{encodeURIComponent(proposalId)}}/${{action}}`, {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{'Content-Type': 'application/json', 'X-Yggy-Ops-Action': 'capability-proposal'}},
          body: JSON.stringify({{reason}}),
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
      setTabCount('proposals', data.counts.open_task_change_proposals || 0);
      setTabCount('capabilities', data.counts.pending_capability_proposals || 0);
      setTabCount('approvals', data.counts.pending_general_approvals || 0);
      document.getElementById('metrics').innerHTML = [
        metric('Service', statusLabel(data.service.status), `worker age ${{text(data.service.worker.age_seconds)}}s`),
        metric('Tasks', data.counts.tasks, `${{data.counts.enabled_tasks}} enabled`),
        metric('Active Runs', data.counts.active_runs, 'queued or running'),
        metric('Pending Reviews', data.counts.pending_reviews || data.counts.pending_approvals, `${{data.counts.pending_task_change_proposals || 0}} task changes; ${{data.counts.pending_capability_proposals || 0}} capabilities; ${{data.counts.pending_general_approvals || 0}} general`),
      ].join('');
      document.getElementById('service').innerHTML = `
        <h2>Service Health</h2>
        <div>Database: ${{statusLabel(data.service.database.connected, data.service.database.connected ? 'connected' : 'degraded')}}</div>
        <div>Worker: ${{statusLabel(data.service.worker.ok, data.service.worker.status)}} <span class="meta">last seen ${{text(data.service.worker.last_seen_at)}}</span></div>
      `;
      syncTaskTypeOptions(data.tasks || []);
      renderTasks();
      if (selectedTaskId && activeView === 'tasks') loadTaskDetail(selectedTaskId);
      if (selectedRunId && activeView === 'runs') loadRunDetail(selectedRunId);
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
        const params = new URLSearchParams({{
          page: String(pageState.audit.page),
          page_size: String(pageSize('audit')),
          sort_by: sortState.audit.by,
          sort_dir: sortState.audit.dir,
        }});
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
        if (data.pagination.total > 0 && pageState.audit.page > data.pagination.total_pages) {{
          pageState.audit.page = data.pagination.total_pages;
          return loadAudit();
        }}
        generated.textContent = `Generated ${{new Date(data.generated_at).toLocaleString()}}; showing ${{data.events.length}} of ${{data.pagination.total}} matching events.`;
        renderTable('audit', [
          sortHeader('audit', 'Time', 'created_at'),
          sortHeader('audit', 'Actor', 'actor_role'),
          sortHeader('audit', 'Action', 'action'),
          sortHeader('audit', 'Resource', 'resource_type'),
          sortHeader('audit', 'Resource ID', 'resource_id'),
          'Detail',
        ], data.events.map(event => [
          esc(event.created_at),
          `<span class="pill">${{esc(event.actor_role)}}</span>`,
          `<code>${{esc(event.action)}}</code>`,
          esc(event.resource_type),
          `<code>${{esc(event.resource_id)}}</code>`,
          jsonBlock(event.detail),
        ]));
        renderPager('audit-pagination', 'audit', data.pagination.total, data.pagination.returned, loadAudit);
        wireSortHeaders('audit', 'audit', loadAudit);
      }} catch (error) {{
        generated.textContent = `Unable to load audit events: ${{error.message}}`;
      }}
    }}
    function wireFilters() {{
      const persistAndRenderTasks = id => {{
        markCustomView();
        persistField(id);
        resetPage('tasks');
        renderTasks();
      }};
      ['task-filter-text', 'task-filter-state', 'task-filter-type'].forEach(id => {{
        byId(id).addEventListener('input', () => persistAndRenderTasks(id));
        byId(id).addEventListener('change', () => persistAndRenderTasks(id));
      }});
      byId('task-page-size').addEventListener('change', () => {{
        resetPage('tasks');
        pageSize('tasks');
        renderTasks();
      }});
      byId('task-filter-clear').addEventListener('click', () => {{
        markCustomView();
        ['task-filter-text', 'task-filter-state', 'task-filter-type'].forEach(id => {{
          byId(id).value = '';
          persistField(id);
        }});
        resetPage('tasks');
        renderTasks();
      }});
      const reloadRuns = id => {{
        markCustomView();
        if (id) persistField(id);
        resetPage('runs');
        loadRuns();
      }};
      ['run-filter-text', 'run-filter-task-id'].forEach(id => {{
        byId(id).addEventListener('input', debounce(() => reloadRuns(id), 350));
      }});
      byId('run-filter-status').addEventListener('change', () => reloadRuns('run-filter-status'));
      byId('run-filter-notification-sent').addEventListener('change', () => reloadRuns('run-filter-notification-sent'));
      byId('run-page-size').addEventListener('change', () => {{
        resetPage('runs');
        pageSize('runs');
        loadRuns();
      }});
      byId('run-filter-clear').addEventListener('click', () => {{
        markCustomView();
        ['run-filter-text', 'run-filter-task-id', 'run-filter-status', 'run-filter-notification-sent'].forEach(id => {{
          byId(id).value = '';
          persistField(id);
        }});
        resetPage('runs');
        loadRuns();
      }});
      const reloadProposals = id => {{
        markCustomView();
        if (id) persistField(id);
        resetPage('proposals');
        loadTaskChangeProposals();
      }};
      ['proposal-filter-q', 'proposal-filter-task-id', 'proposal-filter-requested-by'].forEach(id => {{
        byId(id).addEventListener('input', debounce(() => reloadProposals(id), 350));
      }});
      ['proposal-filter-level', 'proposal-filter-status', 'proposal-filter-risk'].forEach(id => {{
        byId(id).addEventListener('change', () => reloadProposals(id));
      }});
      byId('proposal-page-size').addEventListener('change', () => {{
        resetPage('proposals');
        pageSize('proposals');
        loadTaskChangeProposals();
      }});
      byId('proposal-filter-clear').addEventListener('click', () => {{
        markCustomView();
        ['proposal-filter-q', 'proposal-filter-task-id', 'proposal-filter-requested-by', 'proposal-filter-level', 'proposal-filter-status', 'proposal-filter-risk'].forEach(id => {{
          byId(id).value = '';
          persistField(id);
        }});
        resetPage('proposals');
        loadTaskChangeProposals();
      }});
      const reloadCapabilities = id => {{
        markCustomView();
        if (id) persistField(id);
        resetPage('capabilities');
        loadCapabilityProposals();
      }};
      ['capability-filter-q', 'capability-filter-requested-by', 'capability-filter-channel'].forEach(id => {{
        byId(id).addEventListener('input', debounce(() => reloadCapabilities(id), 350));
      }});
      ['capability-filter-level', 'capability-filter-status'].forEach(id => {{
        byId(id).addEventListener('change', () => reloadCapabilities(id));
      }});
      byId('capability-page-size').addEventListener('change', () => {{
        resetPage('capabilities');
        pageSize('capabilities');
        loadCapabilityProposals();
      }});
      byId('capability-filter-clear').addEventListener('click', () => {{
        markCustomView();
        ['capability-filter-q', 'capability-filter-requested-by', 'capability-filter-channel', 'capability-filter-level', 'capability-filter-status'].forEach(id => {{
          byId(id).value = '';
          persistField(id);
        }});
        resetPage('capabilities');
        loadCapabilityProposals();
      }});
      const reloadApprovals = id => {{
        markCustomView();
        if (id) persistField(id);
        resetPage('approvals');
        loadReviewQueue('approvals');
      }};
      ['approval-filter-q', 'approval-filter-task-id', 'approval-filter-requested-by'].forEach(id => {{
        byId(id).addEventListener('input', debounce(() => reloadApprovals(id), 350));
      }});
      byId('approval-filter-level').addEventListener('change', () => reloadApprovals('approval-filter-level'));
      byId('approval-page-size').addEventListener('change', () => {{
        resetPage('approvals');
        pageSize('approvals');
        loadReviewQueue('approvals');
      }});
      byId('approval-filter-clear').addEventListener('click', () => {{
        markCustomView();
        ['approval-filter-q', 'approval-filter-task-id', 'approval-filter-requested-by', 'approval-filter-level'].forEach(id => {{
          byId(id).value = '';
          persistField(id);
        }});
        resetPage('approvals');
        loadReviewQueue('approvals');
      }});
      const reloadAudit = id => {{
        markCustomView();
        if (id) persistField(id);
        resetPage('audit');
        loadAudit();
      }};
      ['audit-filter-actor', 'audit-filter-action', 'audit-filter-resource-type'].forEach(id => {{
        byId(id).addEventListener('change', () => reloadAudit(id));
      }});
      byId('audit-filter-q').addEventListener('input', debounce(() => reloadAudit('audit-filter-q'), 350));
      byId('audit-filter-resource-id').addEventListener('input', debounce(() => reloadAudit('audit-filter-resource-id'), 350));
      byId('audit-page-size').addEventListener('change', () => {{
        resetPage('audit');
        pageSize('audit');
        loadAudit();
      }});
      byId('audit-filter-clear').addEventListener('click', () => {{
        markCustomView();
        ['audit-filter-q', 'audit-filter-resource-id', 'audit-filter-actor', 'audit-filter-action', 'audit-filter-resource-type'].forEach(id => {{
          byId(id).value = '';
          persistField(id);
        }});
        resetPage('audit');
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
        if (activeView === 'runs') await loadRuns();
        if (activeView === 'audit') await loadAudit();
        if (activeView === 'proposals') await loadTaskChangeProposals();
        if (activeView === 'capabilities') await loadCapabilityProposals();
        if (activeView === 'approvals') await loadReviewQueue('approvals');
      }}
      catch (error) {{ document.getElementById('generated').textContent = `Unable to load status: ${{error.message}}`; }}
    }}
    document.getElementById('refresh').addEventListener('click', refresh);
    document.getElementById('run-refresh').addEventListener('click', loadRuns);
    document.getElementById('audit-refresh').addEventListener('click', loadAudit);
    document.getElementById('proposal-refresh').addEventListener('click', loadTaskChangeProposals);
    document.getElementById('capability-refresh').addEventListener('click', loadCapabilityProposals);
    document.getElementById('approval-refresh').addEventListener('click', () => loadReviewQueue('approvals'));
    document.getElementById('saved-view-select').addEventListener('change', event => applySavedView(event.target.value));
    wireViewTabs();
    restorePersistentFields();
    wireFilters();
    refresh();
    setInterval(refresh, 30000);
  </script>
</body>
</html>
"""
