from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import get_settings
from .schemas import (
    ApprovalLevel,
    N8nWebhookRegistryConfig,
    SourceConfig,
    SourceRegistryConfig,
    TaskConfig,
    _source_identity,
    approval_at_least,
)
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
        "source_policy": {
            "approved_sources_file": "",
            "require_approved_sources_for_task_types": [],
            "require_source_ids": False,
            "allow_web_query_sources": True,
        },
        "n8n_policy": {
            "approved_webhooks_file": "",
            "require_approved_webhooks_for_task_types": [],
            "require_webhook_ids": False,
        },
    }


def load_policy(path: str | None = None) -> dict[str, Any]:
    policy_path = Path(path or get_settings().policy_file)
    if not policy_path.exists():
        return default_policy()
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    merged = default_policy()
    merged.update(data)
    merged["_policy_file"] = str(policy_path)
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
    source_policy = policy.get("source_policy", {})
    if not isinstance(source_policy.get("require_approved_sources_for_task_types", []), list):
        errors.append("source_policy.require_approved_sources_for_task_types must be a list")
    if not isinstance(source_policy.get("require_source_ids", False), bool):
        errors.append("source_policy.require_source_ids must be a boolean")
    if not isinstance(source_policy.get("allow_web_query_sources", True), bool):
        errors.append("source_policy.allow_web_query_sources must be a boolean")
    source_registry_path = source_policy.get("approved_sources_file")
    if source_registry_path:
        try:
            load_source_registry(policy)
        except Exception as exc:
            errors.append(f"approved source registry is invalid: {exc}")
    n8n_policy = policy.get("n8n_policy", {})
    if not isinstance(n8n_policy.get("require_approved_webhooks_for_task_types", []), list):
        errors.append("n8n_policy.require_approved_webhooks_for_task_types must be a list")
    if not isinstance(n8n_policy.get("require_webhook_ids", False), bool):
        errors.append("n8n_policy.require_webhook_ids must be a boolean")
    n8n_registry_path = n8n_policy.get("approved_webhooks_file")
    if n8n_registry_path:
        try:
            load_n8n_webhook_registry(policy)
        except Exception as exc:
            errors.append(f"approved n8n webhook registry is invalid: {exc}")
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

    source_policy = active_policy.get("source_policy", {})
    required_task_types = set(source_policy.get("require_approved_sources_for_task_types", []))
    if task.type in required_task_types:
        errors.extend(validate_task_sources(task, active_policy))

    n8n_policy = active_policy.get("n8n_policy", {})
    n8n_required_task_types = set(n8n_policy.get("require_approved_webhooks_for_task_types", []))
    if task.n8n is not None or task.type in n8n_required_task_types:
        errors.extend(validate_task_n8n_webhook(task, active_policy))

    secret_paths = find_secret_paths(task.model_dump(mode="json"))
    if secret_paths:
        errors.append("plain-text secret-like values found at " + ", ".join(secret_paths))

    if errors:
        raise PolicyViolation(errors)


def load_source_registry(policy: dict[str, Any]) -> SourceRegistryConfig:
    source_policy = policy.get("source_policy", {})
    registry_file = source_policy.get("approved_sources_file")
    if not registry_file:
        return SourceRegistryConfig(version=1, sources=[])
    registry_path = Path(registry_file)
    if not registry_path.is_absolute():
        policy_file = Path(policy.get("_policy_file", get_settings().policy_file))
        registry_path = policy_file.parent / registry_path
    if not registry_path.exists():
        raise FileNotFoundError(registry_path)
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return SourceRegistryConfig.model_validate(data)


def validate_task_sources(task: TaskConfig, policy: dict[str, Any]) -> list[str]:
    source_policy = policy.get("source_policy", {})
    allow_web_query = bool(source_policy.get("allow_web_query_sources", True))
    require_source_ids = bool(source_policy.get("require_source_ids", False))
    registry = load_source_registry(policy)
    approved_by_id = {source.id: source for source in registry.sources if source.enabled}
    approved_by_identity = {
        _source_identity(SourceConfig(type=source.type, url=source.url, query=source.query)): source
        for source in registry.sources
        if source.enabled
    }
    errors: list[str] = []

    for index, source in enumerate(task.sources):
        location = f"sources[{index}]"
        if source.type == "web_query" and not allow_web_query:
            errors.append(f"{location}: web_query sources are disabled by source policy")
        if require_source_ids and not source.source_id:
            errors.append(f"{location}: source_id is required by source policy")
            continue
        if source.source_id:
            approved = approved_by_id.get(source.source_id)
        else:
            approved = approved_by_identity.get(_source_identity(source))
        if not approved:
            detail = source.source_id or source.url or source.query or source.type
            errors.append(f"{location}: source is not in the approved source registry: {detail}")
            continue
        approved_identity = _source_identity(SourceConfig(type=approved.type, url=approved.url, query=approved.query))
        if _source_identity(source) != approved_identity:
            errors.append(f"{location}: source_id {approved.id} does not match the configured source identity")
    return errors


def load_n8n_webhook_registry(policy: dict[str, Any]) -> N8nWebhookRegistryConfig:
    n8n_policy = policy.get("n8n_policy", {})
    registry_file = n8n_policy.get("approved_webhooks_file")
    if not registry_file:
        return N8nWebhookRegistryConfig(version=1, webhooks=[])
    registry_path = Path(registry_file)
    if not registry_path.is_absolute():
        policy_file = Path(policy.get("_policy_file", get_settings().policy_file))
        registry_path = policy_file.parent / registry_path
    if not registry_path.exists():
        raise FileNotFoundError(registry_path)
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return N8nWebhookRegistryConfig.model_validate(data)


def validate_task_n8n_webhook(task: TaskConfig, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    n8n_policy = policy.get("n8n_policy", {})
    require_webhook_ids = bool(n8n_policy.get("require_webhook_ids", False))
    if not task.n8n:
        return ["n8n_webhook task requires n8n config"]
    if require_webhook_ids and not task.n8n.webhook_id:
        errors.append("n8n.webhook_id is required by n8n policy")
        return errors

    registry = load_n8n_webhook_registry(policy)
    approved_by_id = {webhook.id: webhook for webhook in registry.webhooks if webhook.enabled}
    approved = approved_by_id.get(task.n8n.webhook_id) if task.n8n.webhook_id else None
    if not approved:
        errors.append(f"n8n webhook is not in the approved registry: {task.n8n.webhook_id or task.n8n.path}")
        return errors
    if task.n8n.path != approved.path:
        errors.append(f"n8n.webhook_id {approved.id} does not match the configured webhook path")
    if task.n8n.method != approved.method:
        errors.append(f"n8n.webhook_id {approved.id} does not match the configured webhook method")
    if len(task.n8n.payload) > approved.max_payload_keys:
        errors.append(f"n8n payload has more than {approved.max_payload_keys} top-level keys")
    return errors
