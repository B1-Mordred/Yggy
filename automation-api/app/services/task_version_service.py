from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import ApiRole
from app.models import TaskConfigVersionModel, TaskModel
from app.services.validation_service import redact_secrets

MAX_DIFF_PATHS = 80
MAX_DIFF_TEXT = 1200


def record_task_config_version(
    session: Session,
    task: TaskModel,
    *,
    actor_role: ApiRole | str,
    change_type: str,
    approval_id: str | None = None,
    summary: str = "",
) -> TaskConfigVersionModel:
    config = _redacted_config(task.config)
    version = _next_version(session, task.id)
    snapshot = TaskConfigVersionModel(
        task_id=task.id,
        version=version,
        change_type=change_type,
        actor_role=_role_text(actor_role),
        approval_id=approval_id,
        config_hash=config_hash(config),
        summary=summary[:MAX_DIFF_TEXT],
        config=config,
    )
    session.add(snapshot)
    return snapshot


def link_latest_task_config_version_to_approval(
    session: Session,
    task: TaskModel,
    *,
    approval_id: str,
) -> TaskConfigVersionModel | None:
    latest = latest_task_config_version(session, task.id)
    if not latest:
        return None
    if latest.approval_id is not None:
        return None
    if latest.config_hash != config_hash(_redacted_config(task.config)):
        return None
    latest.approval_id = approval_id
    return latest


def ensure_task_config_version_baseline(session: Session) -> int:
    created = 0
    tasks = session.query(TaskModel).order_by(TaskModel.id).all()
    for task in tasks:
        if latest_task_config_version(session, task.id):
            continue
        record_task_config_version(
            session,
            task,
            actor_role="system",
            change_type="bootstrap",
            summary="Initial baseline snapshot for existing task config.",
        )
        created += 1
    if created:
        session.commit()
    return created


def latest_task_config_version(session: Session, task_id: str) -> TaskConfigVersionModel | None:
    return (
        session.query(TaskConfigVersionModel)
        .filter(TaskConfigVersionModel.task_id == task_id)
        .order_by(TaskConfigVersionModel.version.desc())
        .first()
    )


def task_config_versions(
    session: Session,
    task_id: str,
    *,
    limit: int = 10,
) -> list[TaskConfigVersionModel]:
    return (
        session.query(TaskConfigVersionModel)
        .filter(TaskConfigVersionModel.task_id == task_id)
        .order_by(TaskConfigVersionModel.version.desc())
        .limit(limit)
        .all()
    )


def task_config_version_for_approval(
    session: Session,
    approval_id: str,
) -> TaskConfigVersionModel | None:
    return (
        session.query(TaskConfigVersionModel)
        .filter(TaskConfigVersionModel.approval_id == approval_id)
        .order_by(TaskConfigVersionModel.version.desc())
        .first()
    )


def previous_task_config_version(
    session: Session,
    version: TaskConfigVersionModel,
) -> TaskConfigVersionModel | None:
    return (
        session.query(TaskConfigVersionModel)
        .filter(TaskConfigVersionModel.task_id == version.task_id)
        .filter(TaskConfigVersionModel.version < version.version)
        .order_by(TaskConfigVersionModel.version.desc())
        .first()
    )


def task_config_version_to_dict(
    session: Session,
    version: TaskConfigVersionModel,
    *,
    include_config: bool = False,
) -> dict:
    previous = previous_task_config_version(session, version)
    payload = {
        "id": version.id,
        "task_id": version.task_id,
        "version": version.version,
        "change_type": version.change_type,
        "actor_role": version.actor_role,
        "approval_id": version.approval_id,
        "config_hash": version.config_hash,
        "summary": version.summary,
        "created_at": version.created_at,
        "diff": config_diff(previous.config if previous else None, version.config),
    }
    if include_config:
        payload["config"] = version.config
    return payload


def config_hash(config: Any) -> str:
    canonical = json.dumps(_json_safe(config), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def config_diff(before: Any, after: Any, *, max_paths: int = MAX_DIFF_PATHS) -> dict:
    before_flat = _flatten(_redacted_config(before))
    after_flat = _flatten(_redacted_config(after))
    before_paths = set(before_flat)
    after_paths = set(after_flat)
    added_paths = sorted(after_paths - before_paths)
    removed_paths = sorted(before_paths - after_paths)
    changed_paths = sorted(path for path in before_paths & after_paths if before_flat[path] != after_flat[path])
    truncated = False

    def take(paths: list[str]) -> list[str]:
        nonlocal truncated
        remaining = max_paths - emitted_count()
        if remaining <= 0:
            truncated = truncated or bool(paths)
            return []
        selected = paths[:remaining]
        truncated = truncated or len(paths) > len(selected)
        return selected

    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []

    def emitted_count() -> int:
        return len(added) + len(removed) + len(changed)

    for path in take(added_paths):
        added.append({"path": path, "after": after_flat[path]})
    for path in take(removed_paths):
        removed.append({"path": path, "before": before_flat[path]})
    for path in take(changed_paths):
        changed.append({"path": path, "before": before_flat[path], "after": after_flat[path]})

    return {
        "counts": {"added": len(added_paths), "removed": len(removed_paths), "changed": len(changed_paths)},
        "added": added,
        "removed": removed,
        "changed": changed,
        "truncated": truncated,
    }


def _next_version(session: Session, task_id: str) -> int:
    current = session.query(func.max(TaskConfigVersionModel.version)).filter(
        TaskConfigVersionModel.task_id == task_id
    ).scalar()
    return int(current or 0) + 1


def _redacted_config(config: Any) -> Any:
    return redact_secrets(_json_safe(config if isinstance(config, (dict, list)) else {}))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, str):
        return value if len(value) <= MAX_DIFF_TEXT else f"{value[:MAX_DIFF_TEXT]}...<truncated>"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _flatten(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        if not value and path:
            flattened[path] = {}
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            flattened.update(_flatten(child, child_path))
        return flattened
    if isinstance(value, list):
        flattened = {}
        if not value and path:
            flattened[path] = []
        for index, child in enumerate(value):
            flattened.update(_flatten(child, f"{path}[{index}]"))
        return flattened
    return {path or "$": value}


def _role_text(actor_role: ApiRole | str) -> str:
    return actor_role.value if isinstance(actor_role, ApiRole) else str(actor_role)
