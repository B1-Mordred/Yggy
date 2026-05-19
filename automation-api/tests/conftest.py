from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "automation-api"))

from app.database import Base, get_engine, init_db, reset_engine_for_tests  # noqa: E402
from app.main import app  # noqa: E402


TOOL_HEADERS = {"X-Automation-Api-Key": "test-tool-key"}
ADMIN_HEADERS = {"X-Automation-Api-Key": "test-admin-key"}
WORKER_HEADERS = {"X-Automation-Api-Key": "test-worker-key"}
CHANNEL_BRIDGE_HEADERS = {"X-Automation-Api-Key": "test-channel-bridge-key"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AUTOMATION_TOOL_API_KEY", "test-tool-key")
    monkeypatch.setenv("AUTOMATION_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("AUTOMATION_WORKER_API_KEY", "test-worker-key")
    monkeypatch.setenv("AUTOMATION_CHANNEL_BRIDGE_API_KEY", "test-channel-bridge-key")
    monkeypatch.setenv("AUTOMATION_POLICY_FILE", str(ROOT / "configs" / "policies.yaml"))
    monkeypatch.setenv("DISCORD_DRY_RUN", "true")
    reset_engine_for_tests()
    init_db()
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(bind=get_engine())


def sample_task(task_id: str, approval_level: str = "L1_NOTIFY_ONLY", **overrides):
    task = {
        "id": task_id,
        "name": "Sample Task",
        "type": "topic_digest",
        "enabled": False,
        "owner": "local_user",
        "created_by": "yggdrasil",
        "trigger": {"kind": "schedule", "cron": "0 8 * * 1-5", "timezone": "Europe/Berlin"},
        "sources": [
            {
                "source_id": "open_webui_releases",
                "type": "rss",
                "url": "https://github.com/open-webui/open-webui/releases.atom",
            }
        ],
        "filters": {"include": ["Open WebUI"], "exclude": ["sponsored"]},
        "output": {"channel": "discord", "target": "briefings", "format": "5 bullets"},
        "policy": {
            "approval_level": approval_level,
            "max_items": 10,
            "require_sources": True,
            "allow_external_side_effects": False,
            "allow_shell": False,
            "allow_docker_socket": False,
            "allow_filesystem_write": False,
        },
        "runtime": {"dry_run": True, "timeout_seconds": 120, "retry_count": 1},
        "notifications": {
            "on_success": True,
            "on_failure": True,
            "on_empty_result": False,
            "quiet_hours": {
                "enabled": False,
                "start": "22:00",
                "end": "07:00",
                "timezone": "Europe/Berlin",
            },
            "collapse_repeated_failures": True,
            "failure_collapse_window_minutes": 360,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(task.get(key), dict):
            task[key].update(value)
        else:
            task[key] = value
    return task
