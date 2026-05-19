#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

import yaml

from registry_lib import api_key_from_env, api_request, load_local_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reject a task-change proposal with the admin key.")
    parser.add_argument("--proposal-id", required=True, help="Task-change proposal id.")
    parser.add_argument("--reason", default="", help="Short rejection reason.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088", help="Automation API base URL.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_local_env()
    result = api_request(
        "POST",
        f"/task-change-proposals/{args.proposal_id}/reject",
        base_url=args.base_url,
        api_key=api_key_from_env("AUTOMATION_ADMIN_API_KEY"),
        payload={"reason": args.reason},
    )
    print(yaml.safe_dump(result, sort_keys=False, allow_unicode=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
