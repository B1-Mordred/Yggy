#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

sys.path.insert(0, str(ROOT / "automation-api"))
sys.path.insert(0, str(ROOT / "printer-status-exporter"))

from app.policy import load_policy, load_printer_registry  # noqa: E402
from app.schemas import PrinterRegistryConfig  # noqa: E402
from printer_exporter.config import PrinterExporterConfig, load_config as load_exporter_config  # noqa: E402


@dataclass(frozen=True)
class PrinterStatusFinding:
    severity: str
    printer_id: str
    message: str


@dataclass(frozen=True)
class PrinterStatusCheck:
    approved_printer_id: str
    exporter_printer_id: str
    url: str


def parse_exporter_printer_id(url: str) -> tuple[str | None, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None, "approved printer URL scheme must be http or https"
    if parsed.username or parsed.password:
        return None, "approved printer URL must not contain credentials"
    if parsed.hostname != "printer-status-exporter":
        return None, "approved printer URL must point at internal host printer-status-exporter"
    if parsed.port not in {None, 8091}:
        return None, "approved printer URL must use printer-status-exporter port 8091"
    path_parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) != 3 or path_parts[0] != "printers" or path_parts[2] != "supplies":
        return None, "approved printer URL path must be /printers/<printer-id>/supplies"
    return path_parts[1], None


def validate_printer_status_configs(
    *,
    approved_registry: PrinterRegistryConfig,
    exporter_config: PrinterExporterConfig,
    strict_orphans: bool = False,
) -> tuple[list[PrinterStatusFinding], list[PrinterStatusCheck]]:
    findings: list[PrinterStatusFinding] = []
    checks: list[PrinterStatusCheck] = []
    exporter_by_id = {printer.id: printer for printer in exporter_config.printers}
    referenced_exporter_ids: set[str] = set()

    for approved in approved_registry.printers:
        if not approved.enabled:
            continue
        exporter_id, error = parse_exporter_printer_id(approved.url)
        if error:
            findings.append(PrinterStatusFinding("error", approved.id, error))
            continue
        assert exporter_id is not None
        referenced_exporter_ids.add(exporter_id)
        exporter = exporter_by_id.get(exporter_id)
        if exporter is None:
            findings.append(
                PrinterStatusFinding(
                    "error",
                    approved.id,
                    f"internal exporter printer `{exporter_id}` is not configured",
                )
            )
            continue
        if not exporter.enabled:
            findings.append(
                PrinterStatusFinding(
                    "error",
                    approved.id,
                    f"internal exporter printer `{exporter_id}` is disabled",
                )
            )
        if approved.expected_status != 200:
            findings.append(
                PrinterStatusFinding(
                    "error",
                    approved.id,
                    "approved printer expected_status should be 200 for the internal exporter endpoint",
                )
            )
        checks.append(
            PrinterStatusCheck(
                approved_printer_id=approved.id,
                exporter_printer_id=exporter_id,
                url=approved.url,
            )
        )

    orphan_ids = sorted({printer.id for printer in exporter_config.printers if printer.enabled} - referenced_exporter_ids)
    for orphan_id in orphan_ids:
        findings.append(
            PrinterStatusFinding(
                "error" if strict_orphans else "warning",
                orphan_id,
                "enabled exporter printer is not referenced by configs/printers/printers.yaml",
            )
        )

    return findings, checks


def live_check(*, base_url: str, checks: list[PrinterStatusCheck], timeout: float = 5.0) -> list[PrinterStatusFinding]:
    findings: list[PrinterStatusFinding] = []
    clean_base = base_url.rstrip("/")
    for check in checks:
        url = f"{clean_base}/printers/{urllib.parse.quote(check.exporter_printer_id)}/supplies"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                status = response.getcode()
                payload: Any = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            findings.append(PrinterStatusFinding("error", check.approved_printer_id, f"live endpoint returned HTTP {exc.code}"))
            continue
        except Exception as exc:
            findings.append(PrinterStatusFinding("error", check.approved_printer_id, f"live endpoint failed: {exc.__class__.__name__}"))
            continue
        if status != 200:
            findings.append(PrinterStatusFinding("error", check.approved_printer_id, f"live endpoint returned HTTP {status}"))
            continue
        supplies = payload.get("supplies") if isinstance(payload, dict) else None
        if not isinstance(supplies, list) or not supplies:
            findings.append(PrinterStatusFinding("error", check.approved_printer_id, "live endpoint returned no supplies"))
    return findings


def load_inputs(policy_file: Path, exporter_file: Path) -> tuple[PrinterRegistryConfig, PrinterExporterConfig]:
    policy = load_policy(str(policy_file))
    return load_printer_registry(policy), load_exporter_config(exporter_file)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate approved printer registry against the internal printer-status-exporter config.")
    parser.add_argument("--policy-file", type=Path, default=ROOT / "configs" / "policies.yaml")
    parser.add_argument("--exporter-file", type=Path, default=ROOT / "configs" / "printer-status-exporter" / "printers.yaml")
    parser.add_argument("--strict-orphans", action="store_true", help="Fail when enabled exporter printers are not referenced by the approved registry.")
    parser.add_argument("--live", action="store_true", help="Also perform bounded live GET checks against the exporter.")
    parser.add_argument("--base-url", default="http://printer-status-exporter:8091", help="Exporter base URL for --live checks.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        approved_registry, exporter_config = load_inputs(args.policy_file, args.exporter_file)
        findings, checks = validate_printer_status_configs(
            approved_registry=approved_registry,
            exporter_config=exporter_config,
            strict_orphans=args.strict_orphans,
        )
        if args.live:
            findings.extend(live_check(base_url=args.base_url, checks=checks))
    except Exception as exc:
        print(f"Printer status validation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not any(finding.severity == "error" for finding in findings),
                    "checks": [asdict(check) for check in checks],
                    "findings": [asdict(finding) for finding in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        if checks:
            print("Printer status mappings:")
            for check in checks:
                print(f"- {check.approved_printer_id} -> {check.exporter_printer_id} ({check.url})")
        else:
            print("No enabled approved printers configured.")
        for finding in findings:
            print(f"{finding.severity.upper()}: {finding.printer_id}: {finding.message}", file=sys.stderr)
        if not any(finding.severity == "error" for finding in findings):
            print("Printer status validation passed")

    return 1 if any(finding.severity == "error" for finding in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
