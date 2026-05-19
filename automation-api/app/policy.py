from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import yaml

from .config import get_settings
from .schemas import (
    ApprovalLevel,
    N8nWebhookRegistryConfig,
    PrinterRegistryConfig,
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
        "printer_policy": {
            "approved_printers_file": "",
            "require_approved_printers_for_task_types": [],
            "require_printer_ids": False,
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
    printer_policy = policy.get("printer_policy", {})
    if not isinstance(printer_policy.get("require_approved_printers_for_task_types", []), list):
        errors.append("printer_policy.require_approved_printers_for_task_types must be a list")
    if not isinstance(printer_policy.get("require_printer_ids", False), bool):
        errors.append("printer_policy.require_printer_ids must be a boolean")
    printer_registry_path = printer_policy.get("approved_printers_file")
    if printer_registry_path:
        try:
            load_printer_registry(policy)
        except Exception as exc:
            errors.append(f"approved printer registry is invalid: {exc}")
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
        if task.type == "topic_digest" and task.quality.enabled and task.quality.alert_target not in allowed:
            errors.append(f"quality alert target is not whitelisted: {task.quality.alert_target}")

    source_policy = active_policy.get("source_policy", {})
    required_task_types = set(source_policy.get("require_approved_sources_for_task_types", []))
    if task.type in required_task_types:
        errors.extend(validate_task_sources(task, active_policy))

    n8n_policy = active_policy.get("n8n_policy", {})
    n8n_required_task_types = set(n8n_policy.get("require_approved_webhooks_for_task_types", []))
    if task.n8n is not None or task.type in n8n_required_task_types:
        errors.extend(validate_task_n8n_webhook(task, active_policy))

    printer_policy = active_policy.get("printer_policy", {})
    printer_required_task_types = set(printer_policy.get("require_approved_printers_for_task_types", []))
    if task.printer_supplies or task.type in printer_required_task_types:
        errors.extend(validate_task_printer_supplies(task, active_policy))

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
    sources = load_source_registry_sources(registry_path, visited=set())
    return SourceRegistryConfig.model_validate({"version": 1, "sources": sources})


def load_source_registry_sources(path: Path, *, visited: set[Path]) -> list[dict[str, Any]]:
    resolved = path.resolve()
    if resolved in visited:
        raise ValueError(f"recursive source registry include: {path}")
    visited.add(resolved)
    if path.suffix.lower() == ".tsv":
        return source_rows_from_tsv(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    registry = SourceRegistryConfig.model_validate(data)
    sources = [source.model_dump(mode="json") for source in registry.sources]
    for include_file in registry.include_files:
        sources.extend(load_source_registry_sources(path.parent / include_file, visited=visited))
    return sources


def source_rows_from_tsv(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in first_line else "|"
    rows = csv.DictReader(text.splitlines(), delimiter=delimiter)
    sources: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, row in enumerate(rows, start=2):
        if not row or not row.get("Title") or not row.get("URL"):
            continue
        source_id = unique_source_id(slug_source_id(str(row["Title"])), used_ids)
        used_ids.add(source_id)
        ai_safe_fit = str(row.get("AI-safe fit") or "").strip()
        source_type_label = str(row.get("Source type") or "").strip()
        sources.append(
            {
                "id": source_id,
                "name": str(row["Title"]).strip(),
                "type": source_config_type(source_type_label, str(row["URL"])),
                "url": str(row["URL"]).strip(),
                "categories": source_categories_from_row(row),
                "trust_level": trust_level_from_ai_fit(ai_safe_fit),
                "enabled": True,
                "max_items": 5,
                "description": str(row.get("Brief description") or "").strip(),
                "region": str(row.get("Region") or "").strip(),
                "languages": language_values(str(row.get("Language(s)") or "")),
                "source_type_label": source_type_label,
                "update_cadence": str(row.get("Update cadence") or "").strip(),
                "ingestion_notes": str(row.get("Ingestion / license notes") or "").strip(),
                "ai_safe_fit": ai_safe_fit,
                "ingestion_mode": ingestion_mode_from_row(source_type_label, str(row["URL"]), ai_safe_fit),
            }
        )
    return sources


def source_config_type(source_type_label: str, url: str) -> str:
    haystack = f"{source_type_label} {url}".lower()
    if "rss" in haystack or "feed" in haystack or url.endswith((".rss", ".rdf", ".xml")):
        return "rss"
    return "http"


def ingestion_mode_from_row(source_type_label: str, url: str, ai_safe_fit: str) -> str:
    if source_config_type(source_type_label, url) == "rss":
        return "feed_metadata"
    if ai_safe_fit.strip().startswith("A"):
        return "http_summary"
    return "metadata_only"


def trust_level_from_ai_fit(ai_safe_fit: str) -> str:
    if ai_safe_fit.strip().startswith("A"):
        return "ai_safe_a_open"
    if ai_safe_fit.strip().startswith("C"):
        return "ai_safe_c_metadata_only"
    return "ai_safe_b_terms_check"


def source_categories_from_row(row: dict[str, str]) -> list[str]:
    values: list[str] = ["preapproved"]
    for key in ("Category", "Subcategory"):
        values.extend(slug_source_id(part) for part in re.split(r"[:/]", str(row.get(key) or "")) if part.strip())
    for value in language_values(str(row.get("Language(s)") or "")):
        values.append(f"language_{slug_source_id(value)}")
    for value in re.split(r"[/,]", str(row.get("Region") or "")):
        if value.strip():
            values.append(f"region_{slug_source_id(value)}")
    deduped: list[str] = []
    for value in values:
        if len(value) >= 3 and value not in deduped:
            deduped.append(value[:127])
    return deduped or ["preapproved"]


def language_values(value: str) -> list[str]:
    normalized = value.replace("+", "/").replace("regional editions", "regional")
    return [item.strip() for item in re.split(r"[/,]", normalized) if item.strip()]


def slug_source_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug or len(slug) < 3:
        slug = "source"
    if not re.match(r"^[a-z0-9]", slug):
        slug = f"source_{slug}"
    return slug[:120]


def unique_source_id(source_id: str, used_ids: set[str]) -> str:
    if source_id not in used_ids:
        return source_id
    for suffix in range(2, 1000):
        candidate = f"{source_id[:115]}_{suffix}"
        if candidate not in used_ids:
            return candidate
    raise ValueError(f"could not generate unique source id for {source_id}")


def validate_task_sources(task: TaskConfig, policy: dict[str, Any]) -> list[str]:
    source_policy = policy.get("source_policy", {})
    allow_web_query = bool(source_policy.get("allow_web_query_sources", True))
    require_source_ids = bool(source_policy.get("require_source_ids", False))
    registry = load_source_registry(policy)
    all_by_id = {source.id: source for source in registry.sources}
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
            registered = all_by_id.get(source.source_id)
            if registered is not None and not registered.enabled:
                errors.append(f"{location}: source_id {source.source_id} is disabled in the approved source registry")
                continue
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


def load_printer_registry(policy: dict[str, Any]) -> PrinterRegistryConfig:
    printer_policy = policy.get("printer_policy", {})
    registry_file = printer_policy.get("approved_printers_file")
    if not registry_file:
        return PrinterRegistryConfig(version=1, printers=[])
    registry_path = Path(registry_file)
    if not registry_path.is_absolute():
        policy_file = Path(policy.get("_policy_file", get_settings().policy_file))
        registry_path = policy_file.parent / registry_path
    if not registry_path.exists():
        raise FileNotFoundError(registry_path)
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return PrinterRegistryConfig.model_validate(data)


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


def validate_task_printer_supplies(task: TaskConfig, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    printer_policy = policy.get("printer_policy", {})
    require_printer_ids = bool(printer_policy.get("require_printer_ids", False))
    if not task.printer_supplies:
        return ["printer_supply_status task requires printer_supplies config"]

    registry = load_printer_registry(policy)
    approved_by_id = {printer.id: printer for printer in registry.printers if printer.enabled}
    for index, printer in enumerate(task.printer_supplies):
        location = f"printer_supplies[{index}]"
        if require_printer_ids and not printer.printer_id:
            errors.append(f"{location}: printer_id is required by printer policy")
            continue
        approved = approved_by_id.get(printer.printer_id)
        if approved is None:
            errors.append(f"{location}: printer_id {printer.printer_id} is not enabled in printers.yaml")
            continue
        if printer.type != approved.type:
            errors.append(f"{location}: printer_id {approved.id} does not match the configured printer type")
        if printer.url != approved.url:
            errors.append(f"{location}: printer_id {approved.id} does not match the configured printer supply URL")
        if printer.expected_status != approved.expected_status:
            errors.append(f"{location}: printer_id {approved.id} does not match the configured expected status")
    return errors
