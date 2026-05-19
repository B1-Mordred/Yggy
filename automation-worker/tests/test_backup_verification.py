from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from worker.handlers.backup_verification import run_backup_verification


def write_backup(root: Path, *, created_at: datetime | None = None, include_secret_marker: bool = False) -> Path:
    backup_dir = root / "yggy-20260517-120000Z"
    (backup_dir / "api").mkdir(parents=True)
    (backup_dir / "mysql").mkdir(parents=True)
    created = created_at or datetime.now(timezone.utc)
    manifest = {
        "backup_created_at": created.isoformat().replace("+00:00", "Z"),
        "backup_kind": "yggy-local",
        "git_commit": "a" * 40,
        "contains_env_file": False,
        "contains_api_keys": False,
        "contains_discord_tokens": False,
        "contains_dashboard_password": False,
        "files": {
            "mysql_dump": "mysql/automation.sql",
            "tasks": "api/tasks.json",
            "topics": "api/topics.json",
            "openapi": "api/openapi.json",
        },
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (backup_dir / "api" / "health.json").write_text('{"status":"ok"}', encoding="utf-8")
    (backup_dir / "api" / "tasks.json").write_text("[]", encoding="utf-8")
    (backup_dir / "api" / "topics.json").write_text("[]", encoding="utf-8")
    (backup_dir / "api" / "openapi.json").write_text("{}", encoding="utf-8")
    (backup_dir / "git-commit.txt").write_text("a" * 40, encoding="utf-8")
    (backup_dir / "mysql" / "automation.sql").write_text(
        "-- MySQL dump 10.13  Distrib 8.4\nCREATE TABLE tasks (id varchar(128));\n" + ("x" * 2048),
        encoding="utf-8",
    )
    if include_secret_marker:
        (backup_dir / "api" / "bad.json").write_text('{"AUTOMATION_ADMIN_API_KEY":"placeholder"}', encoding="utf-8")
    return backup_dir


def task_config(root: Path) -> dict:
    return {
        "id": "yggy_backup_verification",
        "name": "Yggy Backup Verification",
        "type": "backup_verification",
        "backup": {
            "backup_root": str(root),
            "max_age_hours": 26,
            "min_mysql_dump_bytes": 1024,
            "secret_scan_enabled": True,
        },
        "output": {"channel": "discord", "target": "alerts", "format": "anomalies only"},
        "runtime": {"dry_run": True},
    }


def test_backup_verification_passes_clean_recent_backup(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setenv("YGGY_BACKUP_VERIFY_ROOT", str(root))
    write_backup(root)

    result = run_backup_verification(task_config(root))

    assert result["status"] == "ok"
    assert result["notify"] is False
    assert result["failed_count"] == 0
    assert result["restore_dry_run"]["ok"] is True
    assert result["secret_scan"]["status"] == "clean"
    assert "Discord alert suppressed" in result["message"]


def test_backup_verification_alerts_on_stale_backup(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setenv("YGGY_BACKUP_VERIFY_ROOT", str(root))
    write_backup(root, created_at=datetime.now(timezone.utc) - timedelta(hours=48))

    result = run_backup_verification(task_config(root))

    assert result["status"] == "degraded"
    assert result["notify"] is True
    assert any(item["check"] == "backup_age" for item in result["anomalies"])


def test_backup_verification_secret_scan_reports_file_only(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setenv("YGGY_BACKUP_VERIFY_ROOT", str(root))
    write_backup(root, include_secret_marker=True)

    result = run_backup_verification(task_config(root))

    assert result["status"] == "degraded"
    assert result["secret_scan"]["status"] == "potential_matches"
    assert result["secret_scan"]["files"] == [{"path": "api/bad.json", "match_count": 1}]
    assert "placeholder" not in json.dumps(result)


def test_backup_verification_blocks_roots_outside_allowed_mount(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("YGGY_BACKUP_VERIFY_ROOT", str(allowed))

    result = run_backup_verification(task_config(outside))

    assert result["status"] == "degraded"
    assert result["notify"] is True
    assert result["anomalies"][0]["check"] == "backup_root"
