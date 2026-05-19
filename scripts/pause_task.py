#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Pause an automation task through the local API.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--base-url", default=os.getenv("AUTOMATION_API_BASE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--admin", action="store_true", help="Use AUTOMATION_ADMIN_API_KEY instead of tool key")
    args = parser.parse_args()

    env_name = "AUTOMATION_ADMIN_API_KEY" if args.admin else "AUTOMATION_TOOL_API_KEY"
    api_key = os.getenv(env_name)
    if not api_key:
        print(f"{env_name} is required in the local environment", file=sys.stderr)
        return 2

    response = httpx.post(
        f"{args.base_url.rstrip('/')}/tasks/{args.task_id}/pause",
        headers={"X-Automation-Api-Key": api_key},
        timeout=10,
    )
    if response.status_code >= 400:
        print(response.text, file=sys.stderr)
        return 1
    print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
