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

from registry_lib import api_key_from_env, api_request, load_local_env, load_yaml_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a disabled task-change proposal from a proposed task YAML file.")
    parser.add_argument("--task-id", required=True, help="Existing task id.")
    parser.add_argument("--proposed-config", required=True, type=Path, help="YAML file containing the full proposed task config.")
    parser.add_argument("--summary", default="", help="Human-readable proposal summary.")
    parser.add_argument("--requested-by", default="local_cli", help="Requester label stored on the proposal.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088", help="Automation API base URL.")
    parser.add_argument("--api-key-env", default="AUTOMATION_TOOL_API_KEY", help="API key environment variable.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_local_env()
    payload = {
        "requested_by": args.requested_by,
        "summary": args.summary,
        "proposed_config": load_yaml_file(args.proposed_config),
    }
    result = api_request(
        "POST",
        f"/tasks/{args.task_id}/propose-change",
        base_url=args.base_url,
        api_key=api_key_from_env(args.api_key_env),
        payload=payload,
    )
    print(yaml.safe_dump(result, sort_keys=False, allow_unicode=False), end="")
    print("Keep the nonce local. Approve/apply only with the admin key.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
