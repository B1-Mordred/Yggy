from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import get_settings
from .schemas import ApprovalLevel, TaskConfig, approval_at_least
from .services.validation_service import find_secret_paths


class PolicyViolation(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def default_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "defaults": {
            "timezone": "Europe/Berlin",
            "max_items": 10,
            "require_sources": True,
            "dry_run_new_tasks": True,
        },
        "allowed_discord_targets": ["briefings", "alerts", "approvals"],
        "approval_thresholds": {
            "auto_allow": ["L0_READ_ONLY"],
            "initial_approval_required": ["L1_NOTIFY_ONLY"],
            "admin_required": ["L2_LOCAL_WRITE", "L3_EXTERNAL_SIDE_EFFECT"],
            "manual_only": ["L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE"],
        },
    }


def load_policy(path: str | None = None) -> dict[str, Any]:
    policy_path = Path(path or get_settings().policy_file)
    if not policy_path.exists():
        return default_policy()
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    merged = default_policy()
    merged.update(data)
    return merged


def validate_policy_config(policy: dict[str, Any]) -> None:
    errors: list[str] = []
    if policy.get("version") != 1:
        errors.append("policies.yaml version must be 1")
    if not isinstance(policy.get("allowed_discord_targets"), list):
        errors.append("allowed_discord_targets must be a list")
    thresholds = policy.get("approval_thresholds", {})
    for key in ("auto_allow", "initial_approval_required", "admin_required", "manual_only"):
        for level in thresholds.get(key, []):
            if level not in ApprovalLevel.__members__:
                errors.append(f"unknown approval level in {key}: {level}")
    if errors:
        raise PolicyViolation(errors)


def validate_task_policy(task: TaskConfig, policy: dict[str, Any] | None = None) -> None:
    active_policy = policy or load_policy()
    errors: list[str] = []
    task_policy = task.policy
    level = task_policy.approval_level

    if task_policy.allow_shell:
        errors.append("allow_shell=true is forbidden")
    if task_policy.allow_docker_socket:
        errors.append("allow_docker_socket=true is forbidden")
    if task_policy.allow_external_side_effects and not approval_at_least(level, ApprovalLevel.L3_EXTERNAL_SIDE_EFFECT):
        errors.append("external side effects require L3 or higher")
    if task_policy.allow_filesystem_write and not approval_at_least(level, ApprovalLevel.L2_LOCAL_WRITE):
        errors.append("filesystem writes require L2 or higher")

    if task.output.channel == "discord":
        allowed = set(active_policy.get("allowed_discord_targets", []))
        if task.output.target not in allowed:
            errors.append(f"discord target is not whitelisted: {task.output.target}")

    secret_paths = find_secret_paths(task.model_dump(mode="json"))
    if secret_paths:
        errors.append("plain-text secret-like values found at " + ", ".join(secret_paths))

    if errors:
        raise PolicyViolation(errors)
