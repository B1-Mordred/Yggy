from __future__ import annotations

import secrets
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.auth import ApiRole
from app.models import TaskChangeProposalModel, TaskModel, utcnow
from app.policy import load_policy, validate_task_policy
from app.schemas import ApprovalLevel, TaskChangeProposalCreate, TaskConfig, approval_at_least
from app.services.approval_service import hash_nonce
from app.services.task_version_service import config_diff, config_hash, record_task_config_version
from app.services.validation_service import redact_secrets


class TaskChangeProposalError(ValueError):
    pass


RISKY_PATH_PREFIXES = {
    "enabled": "enablement",
    "trigger.cron": "schedule",
    "trigger.timezone": "schedule",
    "sources": "sources",
    "checks": "checks",
    "output.channel": "output",
    "output.target": "output",
    "runtime.dry_run": "runtime_mode",
    "policy.approval_level": "approval",
    "policy.allow_external_side_effects": "side_effect",
    "policy.allow_filesystem_write": "filesystem",
    "policy.allow_shell": "forbidden_capability",
    "policy.allow_docker_socket": "forbidden_capability",
    "n8n": "n8n",
    "backup.backup_root": "backup_scope",
}


def create_task_change_proposal(
    session: Session,
    task: TaskModel,
    payload: TaskChangeProposalCreate,
    *,
    actor_role: ApiRole,
) -> tuple[TaskChangeProposalModel, str]:
    proposed = payload.proposed_config
    if proposed.id != task.id:
        raise TaskChangeProposalError("proposed_config.id must match task_id")
    validate_task_policy(proposed, load_policy())

    base_config = redact_secrets(task.config)
    proposed_config = redact_secrets(proposed.model_dump(mode="json"))
    diff = config_diff(base_config, proposed_config)
    if _diff_count(diff) == 0:
        raise TaskChangeProposalError("proposed config does not change the task")

    nonce = secrets.token_urlsafe(18)
    risk = risk_for_change(task.config, proposed_config, diff, proposed.policy.approval_level)
    proposal = TaskChangeProposalModel(
        id=str(uuid.uuid4()),
        task_id=task.id,
        status="pending",
        requested_by=payload.requested_by,
        approval_level=proposed.policy.approval_level.value,
        summary=payload.summary or summarize_diff(task.id, diff),
        risk=risk,
        diff=diff,
        base_config_hash=config_hash(base_config),
        base_config=base_config,
        proposed_config=proposed_config,
        nonce_hash=hash_nonce(nonce),
    )
    session.add(proposal)
    return proposal, nonce


def approve_task_change_proposal(proposal: TaskChangeProposalModel, nonce: str) -> None:
    if proposal.status != "pending":
        raise TaskChangeProposalError("proposal is not pending")
    if not secrets.compare_digest(proposal.nonce_hash, hash_nonce(nonce)):
        raise PermissionError("invalid nonce")
    if ApprovalLevel(proposal.approval_level) == ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE:
        raise TaskChangeProposalError("L4 task change proposals are manual only")
    proposal.status = "approved"
    proposal.decided_at = utcnow()


def reject_task_change_proposal(proposal: TaskChangeProposalModel) -> None:
    if proposal.status not in {"pending", "approved"}:
        raise TaskChangeProposalError("proposal cannot be rejected from its current status")
    proposal.status = "rejected"
    proposal.decided_at = utcnow()


def apply_task_change_proposal(
    session: Session,
    proposal: TaskChangeProposalModel,
    *,
    actor_role: ApiRole,
) -> TaskModel:
    if proposal.status != "approved":
        raise TaskChangeProposalError("proposal must be approved before apply")
    if ApprovalLevel(proposal.approval_level) == ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE:
        raise TaskChangeProposalError("L4 task change proposals are manual only")

    task = session.get(TaskModel, proposal.task_id)
    if not task:
        raise TaskChangeProposalError("task no longer exists")
    if config_hash(redact_secrets(task.config)) != proposal.base_config_hash:
        raise TaskChangeProposalError("task config changed since proposal was created")

    proposed = TaskConfig.model_validate(proposal.proposed_config)
    validate_task_policy(proposed, load_policy())
    task.name = proposed.name
    task.type = proposed.type
    task.enabled = proposed.enabled
    task.owner = proposed.owner
    task.created_by = proposed.created_by
    task.approval_level = proposed.policy.approval_level.value
    task.config = proposed.model_dump(mode="json")
    task.status = "enabled" if proposed.enabled else "paused"
    proposal.status = "applied"
    proposal.applied_at = utcnow()
    record_task_config_version(
        session,
        task,
        actor_role=actor_role,
        change_type="proposal_apply",
        approval_id=proposal.id,
        summary=f"Applied task change proposal {proposal.id}: {proposal.summary}",
    )
    return task


def proposal_to_dict(proposal: TaskChangeProposalModel, *, include_configs: bool = False, nonce: str | None = None) -> dict:
    payload = {
        "id": proposal.id,
        "task_id": proposal.task_id,
        "status": proposal.status,
        "requested_by": proposal.requested_by,
        "approval_level": proposal.approval_level,
        "summary": proposal.summary,
        "risk": proposal.risk,
        "diff": proposal.diff,
        "created_at": proposal.created_at,
        "decided_at": proposal.decided_at,
        "applied_at": proposal.applied_at,
    }
    if include_configs:
        payload["base_config"] = proposal.base_config
        payload["proposed_config"] = proposal.proposed_config
        payload["base_config_hash"] = proposal.base_config_hash
    if nonce:
        payload["nonce"] = nonce
    return payload


def risk_for_change(
    base_config: dict[str, Any],
    proposed_config: dict[str, Any],
    diff: dict[str, Any],
    approval_level: ApprovalLevel,
) -> dict[str, Any]:
    categories: dict[str, list[str]] = {}
    for path in changed_paths(diff):
        category = category_for_path(path)
        if category:
            categories.setdefault(category, []).append(path)
    severity = "standard_review"
    if approval_level == ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE:
        severity = "manual_only"
    elif approval_at_least(approval_level, ApprovalLevel.L2_LOCAL_WRITE):
        severity = "admin_required"
    elif categories:
        severity = "operator_review"

    return {
        "severity": severity,
        "approval_level": approval_level.value,
        "categories": categories,
        "requires_admin": approval_level != ApprovalLevel.L0_READ_ONLY or bool(categories),
        "base_enabled": bool(base_config.get("enabled", False)),
        "proposed_enabled": bool(proposed_config.get("enabled", False)),
    }


def changed_paths(diff: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("added", "removed", "changed"):
        for item in diff.get(key, []):
            if isinstance(item, dict) and item.get("path"):
                paths.append(str(item["path"]))
    return paths


def category_for_path(path: str) -> str | None:
    for prefix, category in RISKY_PATH_PREFIXES.items():
        if path == prefix or path.startswith(f"{prefix}.") or path.startswith(f"{prefix}["):
            return category
    return None


def summarize_diff(task_id: str, diff: dict[str, Any]) -> str:
    counts = diff.get("counts") if isinstance(diff.get("counts"), dict) else {}
    return (
        f"Task change proposal for {task_id}: "
        f"{counts.get('changed', 0)} changed, {counts.get('added', 0)} added, {counts.get('removed', 0)} removed paths."
    )


def _diff_count(diff: dict[str, Any]) -> int:
    counts = diff.get("counts") if isinstance(diff.get("counts"), dict) else {}
    return int(counts.get("changed", 0) or 0) + int(counts.get("added", 0) or 0) + int(counts.get("removed", 0) or 0)
