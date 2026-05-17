from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole
from app.config import get_settings
from app.models import RunModel, TaskModel, utcnow
from app.services.validation_service import redact_secrets

CLAIMABLE_RUN_STATUSES = {"queued", "queued_dry_run"}
RUNNING_RUN_STATUSES = {"running", "running_dry_run"}
ACTIVE_RUN_STATUSES = CLAIMABLE_RUN_STATUSES | RUNNING_RUN_STATUSES
COMPLETED_RUN_STATUSES = {"completed"}


def lease_seconds_for_task(task: TaskModel | None) -> int:
    configured = get_settings().run_lease_seconds
    timeout_seconds = 0
    if task and isinstance(task.config, dict):
        runtime = task.config.get("runtime") if isinstance(task.config.get("runtime"), dict) else {}
        try:
            timeout_seconds = int(runtime.get("timeout_seconds") or 0)
        except (TypeError, ValueError):
            timeout_seconds = 0
    return max(configured, timeout_seconds + 60)


def claim_log(task: TaskModel | None, run: RunModel, *, dry_run: bool) -> dict:
    claimed_at = utcnow()
    lease_seconds = lease_seconds_for_task(task)
    expires_at = claimed_at + timedelta(seconds=lease_seconds)
    return redact_secrets(
        {
            "message": "run claimed",
            "dry_run": dry_run,
            "task_id": run.task_id,
            "lease": {
                "claimed_at": claimed_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "seconds": lease_seconds,
            },
        }
    )


def recover_stale_runs(
    session: Session,
    *,
    actor_role: ApiRole | str,
    task_id: str | None = None,
    stale_after_seconds: int | None = None,
    dry_run: bool = False,
    limit: int = 100,
) -> dict:
    now = utcnow()
    query = (
        session.query(RunModel)
        .filter(RunModel.status.in_(RUNNING_RUN_STATUSES))
        .filter(RunModel.completed_at.is_(None))
    )
    if task_id:
        query = query.filter(RunModel.task_id == task_id)

    runs = query.order_by(RunModel.created_at.asc()).limit(limit).with_for_update().all()
    candidates = []
    recovered = []
    for run in runs:
        task = session.get(TaskModel, run.task_id)
        fallback_seconds = stale_after_seconds or lease_seconds_for_task(task)
        expires_at = _run_lease_expires_at(run, fallback_seconds)
        if expires_at > now:
            continue

        detail = {
            "run_id": run.id,
            "task_id": run.task_id,
            "previous_status": run.status,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "lease_expires_at": expires_at.isoformat(),
        }
        candidates.append(detail)
        if dry_run:
            continue

        run.status = "failed_stale_dry_run" if run.status == "running_dry_run" else "failed_stale"
        run.completed_at = now
        current_log = run.log if isinstance(run.log, dict) else {}
        run.log = redact_secrets(
            {
                **current_log,
                "message": "run marked failed because its worker lease expired",
                "stale_recovery": {
                    "recovered_at": now.isoformat(),
                    "previous_status": detail["previous_status"],
                    "lease_expires_at": detail["lease_expires_at"],
                },
            }
        )
        audit_event(
            session,
            actor_role,
            "run.stale_recovered",
            "run",
            run.id,
            {"task_id": run.task_id, "previous_status": detail["previous_status"]},
        )
        recovered.append(detail)

    if recovered and not dry_run:
        session.flush()

    return {
        "dry_run": dry_run,
        "generated_at": now,
        "task_id": task_id,
        "checked": len(runs),
        "candidate_count": len(candidates),
        "recovered_count": len(recovered),
        "candidates": candidates,
        "recovered": recovered,
    }


def _run_lease_expires_at(run: RunModel, fallback_seconds: int) -> datetime:
    log = run.log if isinstance(run.log, dict) else {}
    lease = log.get("lease") if isinstance(log.get("lease"), dict) else {}
    parsed = _parse_datetime(lease.get("expires_at"))
    if parsed:
        return parsed
    created_at = _ensure_aware(run.created_at)
    return created_at + timedelta(seconds=fallback_seconds)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_aware(parsed)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
