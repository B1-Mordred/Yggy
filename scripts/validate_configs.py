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
sys.path.insert(0, str(ROOT / "scripts"))

from app.policy import PolicyViolation, load_policy, validate_policy_config, validate_task_policy  # noqa: E402
from app.schemas import TaskConfig, TopicConfig  # noqa: E402
from app.services.capability_gateway import CapabilityError, validate_capability_registry  # noqa: E402
from exporter.config import load_config as load_metrics_config  # noqa: E402
from task_template_lib import load_templates, render_task_from_template  # noqa: E402


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


def main() -> int:
    errors = (
        validate_policies()
        + validate_topics()
        + validate_tasks()
        + validate_metrics()
        + validate_task_templates()
        + validate_capabilities()
        + validate_identities()
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Config validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
