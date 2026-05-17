from __future__ import annotations

import secrets
from html import escape
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, classify_api_key
from app.config import get_settings
from app.database import get_session
from app.models import ApprovalModel, AuditEventModel, HeartbeatModel, RunModel, TaskModel, utcnow
from app.routers.health import WORKER_HEARTBEAT_MAX_AGE_SECONDS, heartbeat_to_dict
from app.routers.tasks import queue_task_run
from app.schemas import ApprovalLevel, approval_at_least
from app.services.approval_service import approve_request, reject_request, verify_nonce
from app.services.validation_service import redact_secrets

router = APIRouter(tags=["ops"])
basic_security = HTTPBasic(auto_error=False)
OPS_ACTION_HEADER = "approval-decision"
OPS_RUN_ACTION_HEADER = "manual-run"
MAX_RUN_DETAIL_ITEMS = 10
MAX_RUN_DETAIL_ERRORS = 10
MAX_RUN_DETAIL_TEXT = 6000
MAX_RUN_DETAIL_FIELD_TEXT = 1200
MAX_RUN_DETAIL_DEPTH = 5
MAX_RUN_DETAIL_KEYS = 25
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
            _approval_summary(approval, session.get(TaskModel, approval.task_id)) for approval in pending_approvals
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
    audit_event(
        session,
        "ops_dashboard",
        "approval.approve",
        "approval",
        approval.id,
        {"task_id": approval.task_id, "surface": "ops_ui"},
    )
    session.commit()
    return _approval_summary(approval, task)


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
    audit_event(
        session,
        "ops_dashboard",
        "approval.reject",
        "approval",
        approval.id,
        {"task_id": approval.task_id, "surface": "ops_ui", "reason": (payload.reason if payload else "")},
    )
    session.commit()
    return _approval_summary(approval, task)


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


def _bounded_value(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_RUN_DETAIL_DEPTH:
        return "<truncated>"
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= MAX_RUN_DETAIL_KEYS:
                bounded["_truncated_keys"] = len(value) - MAX_RUN_DETAIL_KEYS
                break
            key_text = str(key)
            if _run_detail_secret_key(key_text):
                bounded[key_text] = "[REDACTED]"
            else:
                bounded[key_text] = _bounded_value(child, depth + 1)
        return bounded
    if isinstance(value, list):
        bounded_items = [_bounded_value(item, depth + 1) for item in value[:MAX_RUN_DETAIL_ITEMS]]
        if len(value) > MAX_RUN_DETAIL_ITEMS:
            bounded_items.append({"_truncated_items": len(value) - MAX_RUN_DETAIL_ITEMS})
        return bounded_items
    if isinstance(value, str):
        limit = MAX_RUN_DETAIL_TEXT if depth <= 2 else MAX_RUN_DETAIL_FIELD_TEXT
        return _truncate_text(value, limit)
    return value


def _run_detail_secret_key(key: str) -> bool:
    lower_key = key.lower()
    if lower_key in RUN_DETAIL_NON_SECRET_KEYS:
        return False
    return any(marker in lower_key for marker in RUN_DETAIL_SECRET_KEY_MARKERS)


def _approval_summary(approval: ApprovalModel, task: TaskModel | None = None) -> dict:
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


def _audit_summary(audit: AuditEventModel | None) -> dict | None:
    if audit is None:
        return None
    return {
        "action": audit.action,
        "created_at": audit.created_at,
        "detail": audit.detail,
    }


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
    header, main {{ width: min(1180px, calc(100vw - 32px)); margin: 0 auto; }}
    header {{ padding: 24px 0 12px; display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; }}
    h1 {{ margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 13px; letter-spacing: 0; }}
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
    input {{
      width: min(360px, 100%);
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
    }}
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
    .digest-items {{ margin: 0; padding-left: 22px; }}
    .digest-items li {{ margin: 7px 0; }}
    .run-actions {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .run-actions button {{ padding: 6px 9px; }}
    .run-actions button:disabled {{ cursor: not-allowed; opacity: 0.55; }}
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
  <main>
    <section class="grid" id="metrics"></section>
    <section class="section panel" id="service"></section>
    <section class="section">
      <h2>Tasks</h2>
      <div class="table-wrap"><table id="tasks"></table></div>
      <div class="meta" id="run-action-status"></div>
    </section>
    <section class="section">
      <h2>Recent Runs</h2>
      <div class="table-wrap"><table id="runs"></table></div>
    </section>
    <section class="section panel" id="run-detail">
      <h2>Run Detail</h2>
      <div class="empty">Select a recent run to inspect its digest, n8n response, notification decision, and Discord result.</div>
    </section>
    <section class="section panel" id="approvals"></section>
    <section class="section panel" id="retention"></section>
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
    function renderTable(id, headers, rows) {{
      const table = document.getElementById(id);
      table.innerHTML = `<thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join('')}}</tr></thead>`
        + `<tbody>${{rows.map(row => `<tr>${{row.map(cell => `<td>${{cell}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
    }}
    let selectedRunId = null;
    const runButton = run => `<button type="button" class="link-button" data-run-id="${{esc(run.id)}}" title="${{esc(run.id)}}">${{esc(shortId(run.id))}}</button>`;
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
    function wireTaskRunButtons() {{
      document.querySelectorAll('[data-task-run]').forEach(button => {{
        button.addEventListener('click', () => runTask(button));
      }});
    }}
    async function runTask(button) {{
      const taskId = button.dataset.taskId;
      const mode = button.dataset.runMode;
      const status = document.getElementById('run-action-status');
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
      document.getElementById('generated').textContent = `Generated ${{new Date(data.generated_at).toLocaleString()}}`;
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
      renderTable('tasks', ['Task', 'Type', 'State', 'Trigger', 'Output', 'Latest Run', 'Actions'], data.tasks.map(task => [
        `<code>${{esc(task.id)}}</code><br><span class="meta">${{esc(task.name)}}</span>`,
        `<span class="pill">${{esc(task.type)}}</span><br><span class="meta">${{esc(task.approval_level)}}</span>`,
        `${{statusLabel(task.enabled, task.enabled ? 'enabled' : 'disabled')}}<br><span class="meta">dry run ${{task.dry_run}}</span>`,
        `<code>${{esc(task.trigger.cron)}}</code><br><span class="meta">${{esc(task.trigger.timezone)}}</span>`,
        `${{esc(task.output.channel)}}<br><span class="meta">${{esc(task.output.target)}}</span>`,
        task.latest_run ? `${{runButton(task.latest_run)}} ${{statusLabel(task.latest_run.status)}}<br><span class="meta">${{esc(task.latest_run.completed_at)}}</span>` : '<span class="meta">no runs</span>',
        taskRunButtons(task),
      ]));
      renderTable('runs', ['Run', 'Task', 'Status', 'Result', 'Notification', 'Completed'], data.recent_runs.map(run => [
        runButton(run),
        `<code>${{esc(run.task_id)}}</code>`,
        statusLabel(run.status),
        `${{esc(run.result_status)}}${{run.failed_count !== null && run.failed_count !== undefined ? `<br><span class="meta">failed checks ${{esc(run.failed_count)}}</span>` : ''}}`,
        `${{run.notification.sent === true ? 'sent' : run.notification.sent === false ? 'not sent' : 'n/a'}}<br><span class="meta">${{esc(run.notification.target || run.notification.transport)}}</span>`,
        esc(run.completed_at),
      ]));
      wireRunLinks();
      wireTaskRunButtons();
      if (selectedRunId) loadRunDetail(selectedRunId);
      renderApprovals(data.pending_approvals);
      const latestRetention = data.retention.latest;
      document.getElementById('retention').innerHTML = `
        <h2>Retention</h2>
        <div class="meta">Runs ${{data.retention.policy.run_retention_days}}d, audit ${{data.retention.policy.audit_retention_days}}d, temporary tasks ${{data.retention.policy.temp_task_retention_hours}}h</div>
        ${{latestRetention ? `<div>Latest: <code>${{latestRetention.action}}</code> at ${{text(latestRetention.created_at)}}</div>` : '<div class="empty">No cleanup recorded yet.</div>'}}
      `;
    }}
    async function refresh() {{
      try {{ await loadStatus(); }}
      catch (error) {{ document.getElementById('generated').textContent = `Unable to load status: ${{error.message}}`; }}
    }}
    document.getElementById('refresh').addEventListener('click', refresh);
    refresh();
    setInterval(refresh, 30000);
  </script>
</body>
</html>
"""
