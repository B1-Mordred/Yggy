#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

import yaml

from registry_lib import dump_yaml_file
from task_template_lib import TemplateError, render_task_from_template


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a disabled dry-run task YAML from a Yggy task template.")
    parser.add_argument("template_id", help="Template id, for example topic_digest.")
    parser.add_argument("--id", required=True, dest="task_id", help="Rendered task id.")
    parser.add_argument("--name", required=True, help="Rendered task name.")
    parser.add_argument("--cron", help="Cron schedule override.")
    parser.add_argument("--timezone", help="Timezone override.")
    parser.add_argument("--output-target", help="Output target override.")
    parser.add_argument("--source-id", action="append", dest="source_ids", help="Approved source id. Repeatable.")
    parser.add_argument("--check-id", action="append", dest="check_ids", help="Approved service check id. Repeatable.")
    parser.add_argument("--printer-id", action="append", dest="printer_ids", help="Approved printer id. Repeatable.")
    parser.add_argument("--endpoint-id", action="append", dest="endpoint_ids", help="Approved TLS endpoint id. Repeatable.")
    parser.add_argument("--low-threshold-percent", type=int, help="Printer supply low-threshold percentage.")
    parser.add_argument("--warning-threshold-days", type=int, help="TLS certificate warning threshold in days.")
    parser.add_argument("--critical-threshold-days", type=int, help="TLS certificate critical threshold in days.")
    parser.add_argument("--webhook-id", help="Approved n8n webhook id.")
    parser.add_argument("--n8n-payload-json", help="Small JSON object to place in n8n.payload.")
    parser.add_argument("--include", action="append", help="Include filter term. Repeatable.")
    parser.add_argument("--exclude", action="append", help="Exclude filter term. Repeatable.")
    parser.add_argument("--max-items", type=int, help="Maximum item count override.")
    parser.add_argument("--owner", help="Task owner override.")
    parser.add_argument("--created-by", help="Task creator override.")
    parser.add_argument("--out", type=Path, help="Write YAML to this path instead of stdout.")
    return parser.parse_args()


def values_from_args(args: argparse.Namespace) -> dict[str, Any]:
    n8n_payload = None
    if args.n8n_payload_json is not None:
        try:
            n8n_payload = json.loads(args.n8n_payload_json)
        except json.JSONDecodeError as exc:
            raise TemplateError(f"--n8n-payload-json must be valid JSON: {exc}") from exc
        if not isinstance(n8n_payload, dict):
            raise TemplateError("--n8n-payload-json must decode to a JSON object")

    values: dict[str, Any] = {
        "id": args.task_id,
        "name": args.name,
        "cron": args.cron,
        "timezone": args.timezone,
        "output_target": args.output_target,
        "source_ids": args.source_ids,
        "check_ids": args.check_ids,
        "printer_ids": args.printer_ids,
        "endpoint_ids": getattr(args, "endpoint_ids", None),
        "low_threshold_percent": args.low_threshold_percent,
        "warning_threshold_days": getattr(args, "warning_threshold_days", None),
        "critical_threshold_days": getattr(args, "critical_threshold_days", None),
        "webhook_id": args.webhook_id,
        "n8n_payload": n8n_payload,
        "include": args.include,
        "exclude": args.exclude,
        "max_items": args.max_items,
        "owner": args.owner,
        "created_by": args.created_by,
    }
    return {key: value for key, value in values.items() if value is not None}


def main() -> int:
    args = parse_args()
    try:
        task = render_task_from_template(args.template_id, values_from_args(args))
    except TemplateError as exc:
        print(f"Template render failed: {exc}", file=sys.stderr)
        return 2
    if args.out:
        dump_yaml_file(args.out, task)
        print(f"Wrote {args.out}")
    else:
        print(yaml.safe_dump(task, sort_keys=False, allow_unicode=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
