from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_BACKUP_ROOT = "/app/backups"
DEFAULT_REQUIRED_FILES = [
    "manifest.json",
    "mysql/automation.sql",
    "api/health.json",
    "api/tasks.json",
    "api/topics.json",
    "api/openapi.json",
    "git-commit.txt",
]

SECRET_PATTERNS = [
    re.compile(rb"BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY"),
    re.compile(rb"discord(app)?\.com/api/webhooks", re.IGNORECASE),
    re.compile(rb"\bAUTOMATION_(ADMIN|TOOL|WORKER)_API_KEY\b[\s\"']*[:=]", re.IGNORECASE),
    re.compile(rb"\bDISCORD_(BOT_TOKEN|WEBHOOK_[A-Z0-9_]+)\b[\s\"']*[:=]", re.IGNORECASE),
    re.compile(rb"\bN8N_(ENCRYPTION_KEY|WEBHOOK_SHARED_SECRET)\b[\s\"']*[:=]", re.IGNORECASE),
    re.compile(rb"\bMYSQL_(PASSWORD|ROOT_PASSWORD)\b[\s\"']*[:=]", re.IGNORECASE),
    re.compile(rb"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"),
]


def run_backup_verification(task_config: dict) -> dict:
    backup_config = dict(task_config.get("backup") or {})
    allowed_root = Path(os.getenv("YGGY_BACKUP_VERIFY_ROOT", DEFAULT_BACKUP_ROOT)).resolve()
    configured_root = Path(str(backup_config.get("backup_root") or allowed_root)).resolve()
    anomalies: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    if not _path_is_within(configured_root, allowed_root):
        anomalies.append(
            {
                "check": "backup_root",
                "status": "failed",
                "detail": f"backup_root must be under {allowed_root}",
            }
        )
        return _result(task_config, configured_root, [], None, checks, anomalies, None, None, notify=True)

    backups = _list_backup_dirs(configured_root)
    if not backups:
        anomalies.append({"check": "backup_inventory", "status": "failed", "detail": "no yggy-* backup directories found"})
        return _result(task_config, configured_root, [], None, checks, anomalies, None, None, notify=True)

    latest = backups[0]
    manifest = _load_manifest(latest, checks, anomalies)
    age_hours = _backup_age_hours(latest, manifest)
    max_age_hours = int(backup_config.get("max_age_hours") or 26)
    checks.append({"check": "backup_age", "ok": age_hours <= max_age_hours, "age_hours": round(age_hours, 2), "max_age_hours": max_age_hours})
    if age_hours > max_age_hours:
        anomalies.append(
            {
                "check": "backup_age",
                "status": "failed",
                "detail": f"latest backup is {age_hours:.1f} hours old; maximum is {max_age_hours}",
            }
        )

    restore_dry_run = _restore_dry_run_checks(latest, manifest, backup_config, checks, anomalies)
    secret_scan = _secret_scan(latest, backup_config, checks, anomalies)

    return _result(
        task_config,
        configured_root,
        backups,
        latest,
        checks,
        anomalies,
        restore_dry_run,
        secret_scan,
        manifest=manifest,
        age_hours=age_hours,
        notify=bool(anomalies),
    )


def _path_is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _list_backup_dirs(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    candidates = [path for path in root.iterdir() if path.is_dir() and path.name.startswith("yggy-")]
    return sorted(candidates, key=_backup_sort_key, reverse=True)


def _backup_sort_key(path: Path) -> float:
    manifest_path = path / "manifest.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            created_at = _parse_datetime(data.get("backup_created_at"))
            if created_at:
                return created_at.timestamp()
        except Exception:
            pass
    return path.stat().st_mtime


def _load_manifest(backup_dir: Path, checks: list[dict[str, Any]], anomalies: list[dict[str, Any]]) -> dict[str, Any] | None:
    manifest_path = backup_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        checks.append({"check": "manifest", "ok": False, "error": exc.__class__.__name__})
        anomalies.append({"check": "manifest", "status": "failed", "detail": "manifest.json could not be parsed"})
        return None

    forbidden_flags = {
        "contains_env_file": manifest.get("contains_env_file"),
        "contains_api_keys": manifest.get("contains_api_keys"),
        "contains_discord_tokens": manifest.get("contains_discord_tokens"),
        "contains_dashboard_password": manifest.get("contains_dashboard_password"),
    }
    flagged = [key for key, value in forbidden_flags.items() if value is True]
    checks.append({"check": "manifest", "ok": not flagged, "forbidden_flags": flagged})
    for flag in flagged:
        anomalies.append({"check": "manifest", "status": "failed", "detail": f"manifest flag is true: {flag}"})
    return manifest


def _backup_age_hours(backup_dir: Path, manifest: dict[str, Any] | None) -> float:
    created_at = _parse_datetime(manifest.get("backup_created_at")) if manifest else None
    if not created_at:
        created_at = datetime.fromtimestamp(backup_dir.stat().st_mtime, tz=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds() / 3600)


def _restore_dry_run_checks(
    backup_dir: Path,
    manifest: dict[str, Any] | None,
    backup_config: dict,
    checks: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
) -> dict[str, Any]:
    required_files = list(backup_config.get("required_files") or DEFAULT_REQUIRED_FILES)
    restore_checks: list[dict[str, Any]] = []
    missing: list[str] = []
    for relative in required_files:
        file_path = backup_dir / relative
        ok = file_path.exists() and file_path.is_file()
        restore_checks.append({"check": "required_file", "path": relative, "ok": ok})
        if not ok:
            missing.append(relative)

    if missing:
        anomalies.append({"check": "required_files", "status": "failed", "detail": f"missing files: {', '.join(missing)}"})

    sql_relative = str((manifest or {}).get("files", {}).get("mysql_dump") or "mysql/automation.sql")
    sql_path = backup_dir / sql_relative
    min_bytes = int(backup_config.get("min_mysql_dump_bytes") or 1024)
    sql_check = _mysql_dump_check(sql_path, sql_relative, min_bytes)
    restore_checks.append(sql_check)
    if not sql_check.get("ok"):
        anomalies.append({"check": "mysql_dump", "status": "failed", "detail": sql_check.get("detail") or "MySQL dump check failed"})

    git_commit = str((manifest or {}).get("git_commit") or "").strip()
    git_check = {"check": "git_commit", "ok": bool(re.fullmatch(r"[0-9a-f]{40}", git_commit)), "present": bool(git_commit)}
    restore_checks.append(git_check)
    if not git_check["ok"]:
        anomalies.append({"check": "git_commit", "status": "failed", "detail": "manifest git_commit is missing or invalid"})

    ok = not any(not item.get("ok") for item in restore_checks)
    checks.append({"check": "restore_dry_run", "ok": ok, "failed_count": sum(1 for item in restore_checks if not item.get("ok"))})
    return {"ok": ok, "checks": restore_checks}


def _mysql_dump_check(path: Path, relative: str, min_bytes: int) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {"check": "mysql_dump", "path": relative, "ok": False, "detail": "MySQL dump file is missing"}
    size = path.stat().st_size
    header = b""
    try:
        with path.open("rb") as handle:
            header = handle.read(4096)
    except Exception as exc:
        return {"check": "mysql_dump", "path": relative, "ok": False, "bytes": size, "error": exc.__class__.__name__}
    has_dump_header = b"MySQL dump" in header or b"MariaDB dump" in header
    ok = size >= min_bytes and has_dump_header
    detail = None
    if size < min_bytes:
        detail = f"MySQL dump is {size} bytes; minimum is {min_bytes}"
    elif not has_dump_header:
        detail = "MySQL dump header was not found"
    return {"check": "mysql_dump", "path": relative, "ok": ok, "bytes": size, "has_dump_header": has_dump_header, "detail": detail}


def _secret_scan(
    backup_dir: Path,
    backup_config: dict,
    checks: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
) -> dict[str, Any]:
    enabled = bool(backup_config.get("secret_scan_enabled", True))
    if not enabled:
        result = {"enabled": False, "status": "skipped", "potential_secret_file_count": 0, "files": []}
        checks.append({"check": "secret_scan", "ok": True, "status": "skipped"})
        return result

    max_bytes = int(backup_config.get("max_scan_bytes_per_file") or 2_000_000)
    findings: list[dict[str, Any]] = []
    scanned_files = 0
    for file_path in sorted(path for path in backup_dir.rglob("*") if path.is_file()):
        scanned_files += 1
        try:
            content = file_path.read_bytes()[:max_bytes]
        except Exception:
            continue
        match_count = sum(1 for pattern in SECRET_PATTERNS if pattern.search(content))
        if match_count:
            findings.append({"path": file_path.relative_to(backup_dir).as_posix(), "match_count": match_count})

    if findings:
        anomalies.append({"check": "secret_scan", "status": "failed", "detail": f"potential secrets in {len(findings)} backup files"})

    result = {
        "enabled": True,
        "status": "potential_matches" if findings else "clean",
        "scanned_file_count": scanned_files,
        "potential_secret_file_count": len(findings),
        "files": findings[:25],
    }
    checks.append({"check": "secret_scan", "ok": not findings, "status": result["status"], "potential_secret_file_count": len(findings)})
    return result


def _result(
    task_config: dict,
    root: Path,
    backups: list[Path],
    latest: Path | None,
    checks: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    restore_dry_run: dict[str, Any] | None,
    secret_scan: dict[str, Any] | None,
    *,
    manifest: dict[str, Any] | None = None,
    age_hours: float | None = None,
    notify: bool,
) -> dict:
    latest_summary = None
    if latest is not None:
        sql_relative = str((manifest or {}).get("files", {}).get("mysql_dump") or "mysql/automation.sql")
        sql_path = latest / sql_relative
        latest_summary = {
            "name": latest.name,
            "created_at": (manifest or {}).get("backup_created_at"),
            "age_hours": round(age_hours or _backup_age_hours(latest, manifest), 2),
            "git_commit": str((manifest or {}).get("git_commit") or "")[:12] or None,
            "mysql_dump_bytes": sql_path.stat().st_size if sql_path.exists() else None,
        }

    return {
        "status": "degraded" if anomalies else "ok",
        "backup_root": root.as_posix(),
        "backup_count": len(backups),
        "latest_backup": latest_summary,
        "checks": checks,
        "restore_dry_run": restore_dry_run or {"ok": False, "checks": []},
        "secret_scan": secret_scan or {"enabled": False, "status": "not_run", "potential_secret_file_count": 0, "files": []},
        "anomalies": anomalies,
        "failed_count": len(anomalies),
        "notify": notify,
        "message": render_backup_verification_message(task_config, len(backups), latest_summary, restore_dry_run, secret_scan, anomalies, notify),
    }


def render_backup_verification_message(
    task_config: dict,
    backup_count: int,
    latest_summary: dict[str, Any] | None,
    restore_dry_run: dict[str, Any] | None,
    secret_scan: dict[str, Any] | None,
    anomalies: list[dict[str, Any]],
    notify: bool,
) -> str:
    title = task_config.get("name", "Yggy backup verification")
    dry_run = task_config.get("runtime", {}).get("dry_run", True)
    status = "degraded" if anomalies else "ok"
    lines = [
        f"**{title}**",
        "",
        f"Status: {status}",
        f"Mode: {'dry-run' if dry_run else 'ready'}",
        f"Backups found: `{backup_count}`",
    ]
    if latest_summary:
        lines.extend(
            [
                f"Latest backup: `{latest_summary.get('name')}`",
                f"Backup age: `{latest_summary.get('age_hours')}h`",
                f"MySQL dump bytes: `{latest_summary.get('mysql_dump_bytes')}`",
                f"Git commit: `{latest_summary.get('git_commit') or 'unknown'}`",
            ]
        )
    if restore_dry_run:
        lines.append(f"Restore dry-run checks: `{'ok' if restore_dry_run.get('ok') else 'failed'}`")
    if secret_scan:
        lines.append(f"Secret scan: `{secret_scan.get('status')}`")

    lines.append("")
    if anomalies:
        lines.append("**Anomalies**")
        for anomaly in anomalies[:10]:
            lines.append(f"- `{anomaly.get('check')}`: {anomaly.get('detail', anomaly.get('status', 'failed'))}")
        lines.extend(["", "**Suggested action**", "Run the local backup script and a manual restore dry-run before changing retention or deleting older backups."])
    else:
        lines.append("No backup anomalies detected.")
        if not notify:
            lines.append("Discord alert suppressed by anomaly-only output policy.")
    return "\n".join(lines)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
