#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

import yaml  # noqa: E402

sys.path.insert(0, str(ROOT / "automation-api"))
sys.path.insert(0, str(ROOT / "printer-status-exporter"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.schemas import PrinterRegistryConfig  # noqa: E402
from printer_exporter.config import PrinterExporterConfig  # noqa: E402
from validate_printer_status import validate_printer_status_configs  # noqa: E402


SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
INTERNAL_EXPORTER_BASE_URL = "http://printer-status-exporter:8091"
DEFAULT_EXPORTER_FILE = ROOT / "configs" / "printer-status-exporter" / "printers.yaml"
DEFAULT_APPROVED_FILE = ROOT / "configs" / "printers" / "printers.yaml"
SECRET_QUERY_KEYS = {"api_key", "apikey", "key", "token", "password", "passwd", "secret", "cookie", "nonce"}


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "printers": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    data.setdefault("version", 1)
    data.setdefault("printers", [])
    if not isinstance(data["printers"], list):
        raise ValueError(f"{path}: printers must be a list")
    return data


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")


def validate_printer_id(printer_id: str) -> str:
    if not SLUG_RE.match(printer_id):
        raise ValueError("printer_id must be slug-like, for example office_laser")
    return printer_id


def validate_upstream_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("upstream URL scheme must be http or https")
    if not parsed.hostname:
        raise ValueError("upstream URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("upstream URL must not contain credentials")
    for key, _value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SECRET_QUERY_KEYS or any(marker in key.lower() for marker in SECRET_QUERY_KEYS):
            raise ValueError("upstream URL query must not contain credential-like keys")
    return url


def parse_static_supply(value: str) -> dict[str, Any]:
    if "=" not in value:
        raise ValueError("--static-supply must use NAME=PERCENT")
    name, raw_percent = value.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError("--static-supply name must not be empty")
    try:
        percent = float(raw_percent.strip().rstrip("%"))
    except Exception as exc:
        raise ValueError("--static-supply percent must be numeric") from exc
    if percent < 0 or percent > 100:
        raise ValueError("--static-supply percent must be between 0 and 100")
    level: int | float = int(percent) if percent.is_integer() else percent
    return {"name": name[:120], "level_percent": level, "status": "ok"}


def upsert_printer(printers: list[dict[str, Any]], entry: dict[str, Any], *, force: bool) -> str:
    for index, existing in enumerate(printers):
        if existing.get("id") == entry["id"]:
            if not force:
                raise ValueError(f"printer_id `{entry['id']}` already exists; use --force to update it")
            printers[index] = entry
            return "updated"
    printers.append(entry)
    return "created"


def internal_exporter_url(printer_id: str) -> str:
    return f"{INTERNAL_EXPORTER_BASE_URL}/printers/{urllib.parse.quote(printer_id)}/supplies"


def build_exporter_entry(
    *,
    printer_id: str,
    name: str,
    upstream_url: str | None,
    static_supplies: list[str],
    upstream_expected_status: int,
    timeout_seconds: float,
    enabled: bool,
    description: str,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": printer_id,
        "name": name,
        "enabled": enabled,
        "description": description or f"Operator-configured read-only supply source for {name}.",
    }
    if upstream_url:
        base.update(
            {
                "type": "http_json",
                "url": validate_upstream_url(upstream_url),
                "expected_status": upstream_expected_status,
                "timeout_seconds": timeout_seconds,
            }
        )
        return base
    supplies = [parse_static_supply(item) for item in static_supplies]
    if not supplies:
        supplies = [
            {"name": "Black toner", "level_percent": 75, "status": "ok"},
            {"name": "Cyan toner", "level_percent": 64, "status": "ok"},
        ]
    base.update({"type": "static_json", "supplies": supplies})
    return base


def build_approved_entry(
    *,
    printer_id: str,
    name: str,
    threshold: int,
    enabled: bool,
    description: str,
) -> dict[str, Any]:
    return {
        "id": printer_id,
        "name": name,
        "type": "http_json",
        "url": internal_exporter_url(printer_id),
        "enabled": enabled,
        "default_low_threshold_percent": threshold,
        "expected_status": 200,
        "description": description or f"Approved read-only printer supply endpoint served by the internal printer-status-exporter for {name}.",
    }


def configure_printer_status(
    *,
    printer_id: str,
    name: str,
    upstream_url: str | None = None,
    static_supplies: list[str] | None = None,
    threshold: int = 20,
    upstream_expected_status: int = 200,
    timeout_seconds: float = 3.0,
    enabled: bool = True,
    description: str = "",
    exporter_file: Path = DEFAULT_EXPORTER_FILE,
    approved_file: Path = DEFAULT_APPROVED_FILE,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    printer_id = validate_printer_id(printer_id)
    if threshold < 1 or threshold > 100:
        raise ValueError("--threshold must be between 1 and 100")
    if upstream_expected_status < 100 or upstream_expected_status > 599:
        raise ValueError("--upstream-expected-status must be between 100 and 599")
    if timeout_seconds < 0.2 or timeout_seconds > 15:
        raise ValueError("--timeout-seconds must be between 0.2 and 15")
    if upstream_url and static_supplies:
        raise ValueError("use either --upstream-url or --static-supply, not both")

    static_supplies = static_supplies or []
    exporter_data = load_yaml(exporter_file)
    approved_data = load_yaml(approved_file)
    exporter_entry = build_exporter_entry(
        printer_id=printer_id,
        name=name,
        upstream_url=upstream_url,
        static_supplies=static_supplies,
        upstream_expected_status=upstream_expected_status,
        timeout_seconds=timeout_seconds,
        enabled=enabled,
        description=description,
    )
    approved_entry = build_approved_entry(
        printer_id=printer_id,
        name=name,
        threshold=threshold,
        enabled=enabled,
        description=description,
    )

    exporter_action = upsert_printer(exporter_data["printers"], exporter_entry, force=force)
    approved_action = upsert_printer(approved_data["printers"], approved_entry, force=force)

    approved_model = PrinterRegistryConfig.model_validate(approved_data)
    exporter_model = PrinterExporterConfig.model_validate(exporter_data)
    findings, checks = validate_printer_status_configs(approved_registry=approved_model, exporter_config=exporter_model)
    errors = [finding for finding in findings if finding.severity == "error"]
    if errors:
        details = "; ".join(f"{item.printer_id}: {item.message}" for item in errors)
        raise ValueError(f"generated printer config failed validation: {details}")

    if not dry_run:
        write_yaml(exporter_file, exporter_data)
        write_yaml(approved_file, approved_data)

    return {
        "dry_run": dry_run,
        "printer_id": printer_id,
        "exporter_file": str(exporter_file),
        "approved_file": str(approved_file),
        "exporter_action": exporter_action,
        "approved_action": approved_action,
        "exporter_entry": exporter_entry,
        "approved_entry": approved_entry,
        "checks": [check.__dict__ for check in checks if check.approved_printer_id == printer_id],
        "warnings": [finding.__dict__ for finding in findings if finding.severity == "warning"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Conveniently configure a read-only printer supply source for Yggy.")
    parser.add_argument("--printer-id", required=True, help="Approved printer id, for example office_laser")
    parser.add_argument("--name", required=True, help="Human-readable printer name")
    parser.add_argument("--upstream-url", help="Operator-provided read-only HTTP JSON source for supply levels")
    parser.add_argument("--static-supply", action="append", default=[], metavar="NAME=PERCENT", help="Static dry-run supply level; repeatable")
    parser.add_argument("--threshold", type=int, default=20, help="Low-supply threshold percent for generated Yggy tasks")
    parser.add_argument("--upstream-expected-status", type=int, default=200, help="Expected HTTP status from --upstream-url")
    parser.add_argument("--timeout-seconds", type=float, default=3.0, help="Exporter timeout for --upstream-url")
    parser.add_argument("--description", default="", help="Non-secret description stored in both printer registries")
    parser.add_argument("--disabled", action="store_true", help="Create the entries disabled")
    parser.add_argument("--exporter-file", type=Path, default=DEFAULT_EXPORTER_FILE)
    parser.add_argument("--approved-file", type=Path, default=DEFAULT_APPROVED_FILE)
    parser.add_argument("--force", action="store_true", help="Update an existing printer id")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print changes without writing files")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output")
    args = parser.parse_args()

    try:
        result = configure_printer_status(
            printer_id=args.printer_id,
            name=args.name,
            upstream_url=args.upstream_url,
            static_supplies=args.static_supply,
            threshold=args.threshold,
            upstream_expected_status=args.upstream_expected_status,
            timeout_seconds=args.timeout_seconds,
            enabled=not args.disabled,
            description=args.description,
            exporter_file=args.exporter_file,
            approved_file=args.approved_file,
            force=args.force,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"Printer configuration failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        mode = "Dry-run" if result["dry_run"] else "Configured"
        print(f"{mode} printer `{result['printer_id']}`")
        print(f"- Exporter config: {result['exporter_action']} in {result['exporter_file']}")
        print(f"- Approved registry: {result['approved_action']} in {result['approved_file']}")
        print(f"- Internal URL: {result['approved_entry']['url']}")
        if result["warnings"]:
            print("- Warnings:")
            for warning in result["warnings"]:
                print(f"  - {warning['printer_id']}: {warning['message']}")
        print("Next: run `python scripts/validate_printer_status.py` and `python scripts/validate_configs.py`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
