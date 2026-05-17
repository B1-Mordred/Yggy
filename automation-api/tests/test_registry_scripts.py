from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import export_live_configs  # noqa: E402
import import_task_drafts  # noqa: E402
from conftest import sample_task  # noqa: E402
from registry_lib import diff_registry, format_difference_report, load_yaml_file  # noqa: E402


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
