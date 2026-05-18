#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

sys.path.insert(0, str(ROOT / "automation-api"))
sys.path.insert(0, str(ROOT / "metrics-exporter"))
sys.path.insert(0, str(ROOT / "printer-status-exporter"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.policy import PolicyViolation, load_policy, load_printer_registry, validate_policy_config, validate_task_policy  # noqa: E402
from app.schemas import TaskConfig, TopicConfig  # noqa: E402
from app.services.capability_gateway import CapabilityError, validate_capability_registry  # noqa: E402
from exporter.config import load_config as load_metrics_config  # noqa: E402
from printer_exporter.config import load_config as load_printer_exporter_config  # noqa: E402
from task_template_lib import load_templates, render_task_from_template  # noqa: E402
from validate_printer_status import validate_printer_status_configs  # noqa: E402


SAFE_CHANNEL_CAPABILITIES = {
    "chat",
    "context",
    "memory",
    "draft_task",
    "task_read",
    "run_l1",
    "pause_l1",
}


def has_secret_like_material(value) -> bool:
    text = yaml.safe_dump(value, sort_keys=True, allow_unicode=False).lower()
    markers = (
        "api_key:",
        "apikey:",
        "token:",
        "password:",
        "secret:",
        "webhook_url:",
        "private_key:",
        "cookie:",
        "nonce:",
        "discord.com/api/webhooks/",
        "discordapp.com/api/webhooks/",
    )
    return any(marker in text for marker in markers)


def is_slug_like(value: str) -> bool:
    import re

    return bool(re.match(r"^[a-z][a-z0-9_]{2,127}$", value))


def validate_tasks() -> list[str]:
    errors: list[str] = []
    policy = load_policy(str(ROOT / "configs" / "policies.yaml"))
    for path in sorted((ROOT / "configs" / "tasks").glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            task = TaskConfig.model_validate(data)
            validate_task_policy(task, policy)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    return errors


def validate_topics() -> list[str]:
    errors: list[str] = []
    for path in sorted((ROOT / "configs" / "topics").glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            TopicConfig.model_validate(data)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    return errors


def validate_policies() -> list[str]:
    try:
        validate_policy_config(load_policy(str(ROOT / "configs" / "policies.yaml")))
    except PolicyViolation as exc:
        return [f"{ROOT / 'configs' / 'policies.yaml'}: {exc}"]
    return []


def validate_metrics() -> list[str]:
    errors: list[str] = []
    for path in sorted((ROOT / "configs" / "metrics").glob("*.yaml")):
        try:
            load_metrics_config(path)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    return errors


def validate_printers() -> list[str]:
    errors: list[str] = []
    approved_registry = None
    exporter_configs = []
    try:
        approved_registry = load_printer_registry(load_policy(str(ROOT / "configs" / "policies.yaml")))
    except Exception as exc:
        errors.append(f"{ROOT / 'configs' / 'printers' / 'printers.yaml'}: {exc}")
    for path in sorted((ROOT / "configs" / "printer-status-exporter").glob("*.yaml")):
        try:
            exporter_configs.append((path, load_printer_exporter_config(path)))
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    if approved_registry is not None:
        for path, exporter_config in exporter_configs:
            findings, _checks = validate_printer_status_configs(
                approved_registry=approved_registry,
                exporter_config=exporter_config,
            )
            errors.extend(f"{path}: {finding.printer_id}: {finding.message}" for finding in findings if finding.severity == "error")
    return errors


def validate_task_templates() -> list[str]:
    errors: list[str] = []
    try:
        templates = load_templates()
    except Exception as exc:
        return [f"{ROOT / 'configs' / 'task_templates'}: {exc}"]
    for template_id, template in templates.items():
        try:
            render_task_from_template(
                template_id,
                {
                    "id": f"test_{template_id}_from_template",
                    "name": f"Test {template.name}",
                },
            )
        except Exception as exc:
            errors.append(f"{ROOT / 'configs' / 'task_templates' / (template_id + '.yaml')}: {exc}")
    return errors


def validate_capabilities() -> list[str]:
    try:
        validate_capability_registry(ROOT / "configs" / "capabilities.yaml")
    except CapabilityError as exc:
        return [f"{ROOT / 'configs' / 'capabilities.yaml'}: {exc}"]
    except Exception as exc:
        return [f"{ROOT / 'configs' / 'capabilities.yaml'}: {exc}"]
    return []


def validate_identities() -> list[str]:
    path = ROOT / "configs" / "identities.yaml"
    if not path.exists():
        return [f"{path}: missing identity registry"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return [f"{path}: {exc}"]
    errors: list[str] = []
    if data.get("version") != 1:
        errors.append(f"{path}: version must be 1")
    users = data.get("users")
    if not isinstance(users, list) or not users:
        errors.append(f"{path}: users must be a non-empty list")
        return errors
    seen = set()
    for index, user in enumerate(users):
        if not isinstance(user, dict):
            errors.append(f"{path}: users[{index}] must be an object")
            continue
        user_id = str(user.get("id") or "")
        if not user_id:
            errors.append(f"{path}: users[{index}].id is required")
        elif user_id in seen:
            errors.append(f"{path}: duplicate user id {user_id}")
        seen.add(user_id)
        for channel_index, channel in enumerate(user.get("channels") or []):
            if not isinstance(channel, dict):
                errors.append(f"{path}: users[{index}].channels[{channel_index}] must be an object")
                continue
            if not channel.get("type"):
                errors.append(f"{path}: users[{index}].channels[{channel_index}].type is required")
            if not channel.get("subject_ref"):
                errors.append(f"{path}: users[{index}].channels[{channel_index}].subject_ref is required")
    return errors


def validate_channels() -> list[str]:
    path = ROOT / "configs" / "channels.yaml"
    if not path.exists():
        return [f"{path}: missing channel registry"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return [f"{path}: {exc}"]
    errors: list[str] = []
    if has_secret_like_material(data):
        errors.append(f"{path}: channel registry must not contain secrets, webhook URLs, tokens, passwords, cookies, or nonces")
    if data.get("version") != 1:
        errors.append(f"{path}: version must be 1")
    channels = data.get("channels")
    if not isinstance(channels, list) or not channels:
        errors.append(f"{path}: channels must be a non-empty list")
        return errors
    seen: set[str] = set()
    for index, channel in enumerate(channels):
        prefix = f"{path}: channels[{index}]"
        if not isinstance(channel, dict):
            errors.append(f"{prefix} must be an object")
            continue
        channel_id = str(channel.get("id") or "")
        if not is_slug_like(channel_id):
            errors.append(f"{prefix}.id must be a slug-like id")
        elif channel_id in seen:
            errors.append(f"{prefix}.id duplicates {channel_id}")
        seen.add(channel_id)
        channel_type = channel.get("type")
        if channel_type not in {"openwebui", "discord", "discord_dm"}:
            errors.append(f"{prefix}.type must be openwebui, discord, or discord_dm")
        if not isinstance(channel.get("enabled"), bool):
            errors.append(f"{prefix}.enabled must be a boolean")
        if channel.get("allow_approvals") is not False:
            errors.append(f"{prefix}.allow_approvals must be false for model-facing channels")
        capabilities = channel.get("allowed_capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            errors.append(f"{prefix}.allowed_capabilities must be a non-empty list")
        else:
            unknown = sorted({str(item) for item in capabilities} - SAFE_CHANNEL_CAPABILITIES)
            if unknown:
                errors.append(f"{prefix}.allowed_capabilities contains unsupported values: {', '.join(unknown)}")
        max_chars = channel.get("max_message_chars")
        if not isinstance(max_chars, int) or max_chars < 5 or max_chars > 12000:
            errors.append(f"{prefix}.max_message_chars must be an integer from 5 to 12000")
        if channel_type == "discord":
            if not channel.get("channel_id_ref"):
                errors.append(f"{prefix}.channel_id_ref is required for Discord channels")
            if "webhook" in yaml.safe_dump(channel, sort_keys=True).lower():
                errors.append(f"{prefix} must not reference Discord webhook credentials")
        if channel_type == "discord_dm":
            if channel.get("channel_id_ref"):
                errors.append(f"{prefix}.channel_id_ref must not be used for Discord DM channels")
            if not channel.get("allowed_user_ids_ref"):
                errors.append(f"{prefix}.allowed_user_ids_ref is required for Discord DM channels")
            if "webhook" in yaml.safe_dump(channel, sort_keys=True).lower():
                errors.append(f"{prefix} must not reference Discord webhook credentials")
    return errors


def main() -> int:
    errors = (
        validate_policies()
        + validate_topics()
        + validate_tasks()
        + validate_metrics()
        + validate_printers()
        + validate_task_templates()
        + validate_capabilities()
        + validate_identities()
        + validate_channels()
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Config validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
