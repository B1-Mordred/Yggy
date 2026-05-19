from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole
from app.models import ApprovalModel, AuditEventModel, RunModel, TaskConfigVersionModel, TaskModel, utcnow

TEMP_TASK_PREFIXES = ("temporary_", "test_")
TEMP_TASK_STATUSES = ("paused", "draft", "pending_approval", "rejected", "archived")


@dataclass(frozen=True)
class RetentionPolicy:
    run_retention_days: int
    audit_retention_days: int
    temp_task_retention_hours: int


def apply_retention(
    session: Session,
    *,
    actor_role: ApiRole,
    policy: RetentionPolicy,
    dry_run: bool = False,
) -> dict:
    now = utcnow()
    run_cutoff = now - timedelta(days=policy.run_retention_days)
    audit_cutoff = now - timedelta(days=policy.audit_retention_days)
    temp_task_cutoff = now - timedelta(hours=policy.temp_task_retention_hours)

    run_query = _old_completed_runs(session, run_cutoff)
    audit_query = _old_audit_events(session, audit_cutoff)
    temp_task_ids = _temporary_task_ids(session, temp_task_cutoff)

    counts = {
        "runs": run_query.count(),
        "audit_events": audit_query.count(),
        "temporary_tasks": len(temp_task_ids),
        "temporary_task_approvals": _temporary_task_approvals(session, temp_task_ids).count() if temp_task_ids else 0,
        "temporary_task_config_versions": _temporary_task_config_versions(session, temp_task_ids).count()
        if temp_task_ids
        else 0,
    }

    deleted = {key: 0 for key in counts}
    if not dry_run:
        deleted["runs"] = run_query.delete(synchronize_session=False)
        deleted["audit_events"] = audit_query.delete(synchronize_session=False)
        if temp_task_ids:
            deleted["temporary_task_approvals"] = _temporary_task_approvals(session, temp_task_ids).delete(
                synchronize_session=False
            )
            deleted["temporary_task_config_versions"] = _temporary_task_config_versions(
                session, temp_task_ids
            ).delete(synchronize_session=False)
            deleted["temporary_tasks"] = (
                session.query(TaskModel).filter(TaskModel.id.in_(temp_task_ids)).delete(synchronize_session=False)
            )

    result = {
        "dry_run": dry_run,
        "policy": {
            "run_retention_days": policy.run_retention_days,
            "audit_retention_days": policy.audit_retention_days,
            "temp_task_retention_hours": policy.temp_task_retention_hours,
        },
        "cutoffs": {
            "runs_completed_before": run_cutoff,
            "audit_events_before": audit_cutoff,
            "temporary_tasks_created_before": temp_task_cutoff,
        },
        "matched": counts,
        "deleted": deleted,
        "temporary_task_ids": temp_task_ids,
    }
    audit_event(
        session,
        actor_role,
        "maintenance.retention.preview" if dry_run else "maintenance.retention.apply",
        "maintenance",
        "retention",
        {"matched": counts, "deleted": deleted, "dry_run": dry_run},
    )
    session.commit()
    return result


def _old_completed_runs(session: Session, cutoff: datetime):
    return session.query(RunModel).filter(RunModel.completed_at.isnot(None)).filter(RunModel.completed_at < cutoff)


def _old_audit_events(session: Session, cutoff: datetime):
    return session.query(AuditEventModel).filter(AuditEventModel.created_at < cutoff)


def _temporary_task_ids(session: Session, cutoff: datetime) -> list[str]:
    prefix_filter = or_(*(TaskModel.id.like(f"{prefix}%") for prefix in TEMP_TASK_PREFIXES))
    rows = (
        session.query(TaskModel.id)
        .filter(prefix_filter)
        .filter(TaskModel.enabled.is_(False))
        .filter(TaskModel.status.in_(TEMP_TASK_STATUSES))
        .filter(TaskModel.created_at < cutoff)
        .order_by(TaskModel.id)
        .all()
    )
    return [row[0] for row in rows]


def _temporary_task_approvals(session: Session, task_ids: list[str]):
    return session.query(ApprovalModel).filter(ApprovalModel.task_id.in_(task_ids))


def _temporary_task_config_versions(session: Session, task_ids: list[str]):
    return session.query(TaskConfigVersionModel).filter(TaskConfigVersionModel.task_id.in_(task_ids))
