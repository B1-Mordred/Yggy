#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    parser.add_argument("--include", action="append", help="Include filter term. Repeatable.")
    parser.add_argument("--exclude", action="append", help="Exclude filter term. Repeatable.")
    parser.add_argument("--max-items", type=int, help="Maximum item count override.")
    parser.add_argument("--owner", help="Task owner override.")
    parser.add_argument("--created-by", help="Task creator override.")
    parser.add_argument("--out", type=Path, help="Write YAML to this path instead of stdout.")
    return parser.parse_args()


def values_from_args(args: argparse.Namespace) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": args.task_id,
        "name": args.name,
        "cron": args.cron,
        "timezone": args.timezone,
        "output_target": args.output_target,
        "source_ids": args.source_ids,
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
