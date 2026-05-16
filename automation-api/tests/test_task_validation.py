from __future__ import annotations

from pathlib import Path

import yaml

from app.policy import load_policy, validate_policy_config, validate_task_policy
from app.schemas import TaskConfig

ROOT = Path(__file__).resolve().parents[2]


def test_example_yaml_files_validate():
    policy = load_policy(str(ROOT / "configs" / "policies.yaml"))
    validate_policy_config(policy)
    for path in sorted((ROOT / "configs" / "tasks").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        task = TaskConfig.model_validate(data)
        validate_task_policy(task, policy)


def test_invalid_cron_fails():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_daily_briefing.yaml").read_text(encoding="utf-8"))
    data["id"] = "invalid_cron"
    data["trigger"]["cron"] = "not a cron"
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "cron" in str(exc)
    else:
        raise AssertionError("invalid cron was accepted")


def test_invalid_timezone_fails():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_daily_briefing.yaml").read_text(encoding="utf-8"))
    data["id"] = "invalid_timezone"
    data["trigger"]["timezone"] = "No/Such_Zone"
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "timezone" in str(exc)
    else:
        raise AssertionError("invalid timezone was accepted")
