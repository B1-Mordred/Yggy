from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import configure_printer_status  # noqa: E402
import export_live_configs  # noqa: E402
import import_task_drafts  # noqa: E402
import validate_printer_status  # noqa: E402
from conftest import sample_task  # noqa: E402
from registry_lib import diff_registry, format_difference_report, load_local_env, load_yaml_file  # noqa: E402


def sample_topic(topic_id: str = "local_ai_security") -> dict:
    return {
        "id": topic_id,
        "name": "Local AI Security",
        "enabled": False,
        "owner": "local_user",
        "created_by": "yggdrasil",
        "description": "Local AI security sources",
        "keywords": ["Open WebUI", "Ollama"],
        "sources": [
            {
                "source_id": "open_webui_releases",
                "type": "rss",
                "url": "https://github.com/open-webui/open-webui/releases.atom",
            }
        ],
    }


def test_diff_registry_detects_risky_task_changes():
    local = sample_task("registry_diff_task")
    live = sample_task("registry_diff_task")
    live["trigger"]["cron"] = "30 9 * * 1-5"
    live["output"]["target"] = "alerts"
    live["policy"]["approval_level"] = "L2_LOCAL_WRITE"
    live["runtime"]["dry_run"] = False

    differences = diff_registry(local_tasks={local["id"]: local}, live_tasks={live["id"]: live})
    paths = {difference.path: difference.risk for difference in differences}

    assert paths["$.trigger.cron"] == "schedule"
    assert paths["$.output.target"] == "output"
    assert paths["$.policy.approval_level"] == "approval"
    assert paths["$.runtime.dry_run"] == "runtime_mode"
    assert "registry_diff_task" in format_difference_report(differences)


def test_diff_registry_detects_yaml_only_and_live_only_tasks():
    local = sample_task("yaml_only_task")
    live = sample_task("live_only_task")

    differences = diff_registry(local_tasks={local["id"]: local}, live_tasks={live["id"]: live})
    kinds = {(difference.resource_id, difference.kind) for difference in differences}

    assert ("yaml_only_task", "missing_live") in kinds
    assert ("live_only_task", "missing_yaml") in kinds


def test_load_local_env_ignores_unreadable_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("SHOULD_NOT_BE_SET=value\n", encoding="utf-8")

    original_read_text = Path.read_text

    def guarded_read_text(self, *args, **kwargs):
        if self == env_path:
            raise PermissionError("not readable by this service account")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.delenv("SHOULD_NOT_BE_SET", raising=False)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    load_local_env(tmp_path)

    assert "SHOULD_NOT_BE_SET" not in os.environ


def test_export_live_configs_writes_generated_yaml(tmp_path, monkeypatch):
    task = sample_task("exported_task")
    topic = sample_topic("exported_topic")

    monkeypatch.setattr(export_live_configs, "fetch_live_tasks", lambda base_url, api_key: {task["id"]: {"id": task["id"], "enabled": False, "config": task}})
    monkeypatch.setattr(export_live_configs, "fetch_live_topics", lambda base_url, api_key: {topic["id"]: {"id": topic["id"], "enabled": False, "config": topic}})

    manifest = export_live_configs.export_live_configs(
        base_url="http://127.0.0.1:8088",
        out_dir=tmp_path / "exports" / "live",
        api_key="test-admin-key",
        clean=True,
    )

    assert manifest["task_count"] == 1
    assert manifest["topic_count"] == 1
    assert load_yaml_file(tmp_path / "exports" / "live" / "tasks" / "exported_task.yaml")["id"] == "exported_task"
    assert load_yaml_file(tmp_path / "exports" / "live" / "topics" / "exported_topic.yaml")["id"] == "exported_topic"
    assert json.loads((tmp_path / "exports" / "live" / "manifest.json").read_text(encoding="utf-8"))["tasks"] == ["exported_task"]


def test_import_draft_payload_forces_disabled_even_if_yaml_enabled():
    task = sample_task("enabled_yaml_task")
    task["enabled"] = True

    payload = import_task_drafts.disabled_draft_payload(task)

    assert payload["enabled"] is False


def test_import_draft_payload_rejects_secret_like_yaml():
    task = sample_task("secret_yaml_task")
    task["filters"]["include"] = ["sk-" + "notarealsecretvalue1234567890"]

    with pytest.raises(Exception) as exc:
        import_task_drafts.disabled_draft_payload(task)

    assert "secret-like" in str(exc.value) or "plain-text secret" in str(exc.value)


def test_import_skips_existing_tasks_without_update(tmp_path, monkeypatch):
    task = sample_task("existing_task")
    task_dir = tmp_path / "configs" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "existing_task.yaml").write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(import_task_drafts, "fetch_live_tasks", lambda base_url, api_key: {"existing_task": {"id": "existing_task", "config": task}})

    def fail_api_request(*args, **kwargs):
        raise AssertionError("existing task should not be overwritten without --update-existing")

    monkeypatch.setattr(import_task_drafts, "api_request", fail_api_request)

    actions = import_task_drafts.import_task_drafts(
        base_url="http://127.0.0.1:8088",
        api_key="test-admin-key",
        config_dir=tmp_path / "configs",
    )

    assert actions == [
        {
            "task_id": "existing_task",
            "exists": True,
            "queued_approval_request": False,
            "approval_id": None,
            "nonce": None,
            "nonce_omitted": False,
            "initial_approval_rejected": False,
            "action": "skipped_existing",
        }
    ]


def test_import_request_approval_requires_print_nonces(tmp_path, monkeypatch):
    task = sample_task("approval_task")
    task_dir = tmp_path / "configs" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "approval_task.yaml").write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        import_task_drafts.import_task_drafts(
            base_url="http://127.0.0.1:8088",
            api_key="test-admin-key",
            config_dir=tmp_path / "configs",
            request_approval=True,
        )

    assert "--print-nonces" in str(exc.value)


def test_validate_printer_status_accepts_internal_exporter_mapping():
    approved = validate_printer_status.PrinterRegistryConfig.model_validate(
        {
            "version": 1,
            "printers": [
                {
                    "id": "office_laser",
                    "name": "Office Laser",
                    "type": "http_json",
                    "url": "http://printer-status-exporter:8091/printers/office_laser/supplies",
                    "enabled": True,
                }
            ],
        }
    )
    exporter = validate_printer_status.PrinterExporterConfig.model_validate(
        {
            "version": 1,
            "printers": [
                {
                    "id": "office_laser",
                    "name": "Office Laser",
                    "type": "static_json",
                    "supplies": [{"name": "Black toner", "level_percent": 75}],
                }
            ],
        }
    )

    findings, checks = validate_printer_status.validate_printer_status_configs(
        approved_registry=approved,
        exporter_config=exporter,
    )

    assert findings == []
    assert checks[0].approved_printer_id == "office_laser"
    assert checks[0].exporter_printer_id == "office_laser"


def test_validate_printer_status_rejects_external_approved_url():
    approved = validate_printer_status.PrinterRegistryConfig.model_validate(
        {
            "version": 1,
            "printers": [
                {
                    "id": "office_laser",
                    "name": "Office Laser",
                    "type": "http_json",
                    "url": "http://192.168.2.55/supplies",
                    "enabled": True,
                }
            ],
        }
    )
    exporter = validate_printer_status.PrinterExporterConfig.model_validate({"version": 1, "printers": []})

    findings, checks = validate_printer_status.validate_printer_status_configs(
        approved_registry=approved,
        exporter_config=exporter,
    )

    assert checks == []
    assert findings[0].severity == "error"
    assert "internal host printer-status-exporter" in findings[0].message


def test_configure_printer_status_writes_exporter_and_approved_registry(tmp_path):
    exporter_file = tmp_path / "configs" / "printer-status-exporter" / "printers.yaml"
    approved_file = tmp_path / "configs" / "printers" / "printers.yaml"

    result = configure_printer_status.configure_printer_status(
        printer_id="office_laser",
        name="Office Laser",
        upstream_url="http://printer-adapter.local/supplies",
        threshold=15,
        exporter_file=exporter_file,
        approved_file=approved_file,
    )

    exporter = yaml.safe_load(exporter_file.read_text(encoding="utf-8"))
    approved = yaml.safe_load(approved_file.read_text(encoding="utf-8"))

    assert result["exporter_action"] == "created"
    assert result["approved_action"] == "created"
    assert exporter["printers"][0]["id"] == "office_laser"
    assert exporter["printers"][0]["type"] == "http_json"
    assert exporter["printers"][0]["url"] == "http://printer-adapter.local/supplies"
    assert approved["printers"][0]["url"] == "http://printer-status-exporter:8091/printers/office_laser/supplies"
    assert approved["printers"][0]["default_low_threshold_percent"] == 15


def test_configure_printer_status_dry_run_does_not_write(tmp_path):
    exporter_file = tmp_path / "configs" / "printer-status-exporter" / "printers.yaml"
    approved_file = tmp_path / "configs" / "printers" / "printers.yaml"

    result = configure_printer_status.configure_printer_status(
        printer_id="dry_run_printer",
        name="Dry Run Printer",
        static_supplies=["Black toner=70"],
        exporter_file=exporter_file,
        approved_file=approved_file,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["exporter_entry"]["type"] == "static_json"
    assert not exporter_file.exists()
    assert not approved_file.exists()


def test_configure_printer_status_duplicate_requires_force(tmp_path):
    exporter_file = tmp_path / "configs" / "printer-status-exporter" / "printers.yaml"
    approved_file = tmp_path / "configs" / "printers" / "printers.yaml"

    configure_printer_status.configure_printer_status(
        printer_id="office_laser",
        name="Office Laser",
        static_supplies=["Black toner=80"],
        exporter_file=exporter_file,
        approved_file=approved_file,
    )

    with pytest.raises(ValueError, match="--force"):
        configure_printer_status.configure_printer_status(
            printer_id="office_laser",
            name="Office Laser Updated",
            static_supplies=["Black toner=70"],
            exporter_file=exporter_file,
            approved_file=approved_file,
        )

    result = configure_printer_status.configure_printer_status(
        printer_id="office_laser",
        name="Office Laser Updated",
        static_supplies=["Black toner=70"],
        exporter_file=exporter_file,
        approved_file=approved_file,
        force=True,
    )

    assert result["exporter_action"] == "updated"
    assert result["approved_action"] == "updated"


def test_configure_printer_status_rejects_credential_url(tmp_path):
    with pytest.raises(ValueError, match="credentials|credential-like"):
        configure_printer_status.configure_printer_status(
            printer_id="office_laser",
            name="Office Laser",
            upstream_url="http://user:pass@printer-adapter.local/supplies",
            exporter_file=tmp_path / "exporter.yaml",
            approved_file=tmp_path / "approved.yaml",
        )

    with pytest.raises(ValueError, match="credential-like"):
        configure_printer_status.configure_printer_status(
            printer_id="office_laser",
            name="Office Laser",
            upstream_url="http://printer-adapter.local/supplies?api_token=abc",
            exporter_file=tmp_path / "exporter.yaml",
            approved_file=tmp_path / "approved.yaml",
        )
