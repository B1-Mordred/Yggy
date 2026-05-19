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


def test_invalid_notification_quiet_hour_fails():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_daily_briefing.yaml").read_text(encoding="utf-8"))
    data["id"] = "invalid_quiet_hour"
    data["notifications"]["quiet_hours"]["start"] = "25:00"
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "quiet hour" in str(exc)
    else:
        raise AssertionError("invalid quiet hour was accepted")


def test_invalid_failure_collapse_window_fails():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_daily_briefing.yaml").read_text(encoding="utf-8"))
    data["id"] = "invalid_failure_collapse_window"
    data["notifications"]["failure_collapse_window_minutes"] = 0
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "failure_collapse_window_minutes" in str(exc)
    else:
        raise AssertionError("invalid failure collapse window was accepted")


def test_quality_alert_target_must_be_whitelisted():
    policy = load_policy(str(ROOT / "configs" / "policies.yaml"))
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_daily_briefing.yaml").read_text(encoding="utf-8"))
    data["id"] = "invalid_quality_alert_target"
    data["quality"] = {"alert_target": "not_allowed"}
    task = TaskConfig.model_validate(data)
    try:
        validate_task_policy(task, policy)
    except Exception as exc:
        assert "quality alert target is not whitelisted" in str(exc)
    else:
        raise AssertionError("unapproved quality alert target was accepted")


def test_n8n_webhook_requires_n8n_config():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_n8n_webhook.yaml").read_text(encoding="utf-8"))
    data["id"] = "missing_n8n_config"
    data.pop("n8n")
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "n8n_webhook task requires n8n config" in str(exc)
    else:
        raise AssertionError("n8n_webhook task without n8n config was accepted")


def test_backup_verification_requires_backup_config():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_backup_verification.yaml").read_text(encoding="utf-8"))
    data["id"] = "missing_backup_config"
    data.pop("backup")
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "backup_verification task requires backup config" in str(exc)
    else:
        raise AssertionError("backup_verification task without backup config was accepted")


def test_printer_supply_status_requires_printer_supplies_config():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_printer_supply_status.yaml").read_text(encoding="utf-8"))
    data["id"] = "missing_printer_supplies_config"
    data.pop("printer_supplies")
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "printer_supply_status task requires printer_supplies config" in str(exc)
    else:
        raise AssertionError("printer_supply_status task without printer_supplies config was accepted")


def test_printer_supply_status_rejects_unapproved_printer_id():
    policy = load_policy(str(ROOT / "configs" / "policies.yaml"))
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_printer_supply_status.yaml").read_text(encoding="utf-8"))
    data["id"] = "unapproved_printer_supply"
    data["printer_supplies"][0]["printer_id"] = "unknown_printer"
    task = TaskConfig.model_validate(data)
    try:
        validate_task_policy(task, policy)
    except Exception as exc:
        assert "not enabled in printers.yaml" in str(exc)
    else:
        raise AssertionError("printer_supply_status task accepted an unapproved printer_id")


def test_backup_verification_backup_root_must_use_worker_mount():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_backup_verification.yaml").read_text(encoding="utf-8"))
    data["id"] = "bad_backup_root"
    data["backup"]["backup_root"] = "/srv/Yggy/backups"
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "backup_root must be under" in str(exc)
    else:
        raise AssertionError("backup_verification task accepted a host backup path")


def test_n8n_webhook_rejects_absolute_url_path():
    data = yaml.safe_load((ROOT / "configs" / "tasks" / "example_n8n_webhook.yaml").read_text(encoding="utf-8"))
    data["id"] = "bad_n8n_absolute_url"
    data["n8n"]["path"] = "https://example.com/webhook"
    try:
        TaskConfig.model_validate(data)
    except Exception as exc:
        assert "path must start" in str(exc) or "absolute URL" in str(exc)
    else:
        raise AssertionError("absolute n8n webhook URL was accepted")
