#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

from task_template_lib import load_templates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List available Yggy task templates.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    templates = load_templates()
    summaries = [template.summary() for template in templates.values()]
    if args.json:
        print(json.dumps(summaries, indent=2, sort_keys=True))
        return 0

    if not summaries:
        print("No task templates found.")
        return 0

    print("Task templates:")
    for summary in summaries:
        targets = ", ".join(summary["allowed_output_targets"])
        print(
            f"- {summary['id']}: {summary['name']} "
            f"({summary['task_type']}, {summary['default_approval_level']}, targets: {targets})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
