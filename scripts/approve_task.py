#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve an automation task through the local API.")
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--base-url", default=os.getenv("AUTOMATION_API_BASE_URL", "http://127.0.0.1:8088"))
    args = parser.parse_args()

    api_key = os.getenv("AUTOMATION_ADMIN_API_KEY")
    if not api_key:
        print("AUTOMATION_ADMIN_API_KEY is required in the local environment", file=sys.stderr)
        return 2

    response = httpx.post(
        f"{args.base_url.rstrip('/')}/approvals/{args.approval_id}/approve",
        headers={"X-Automation-Api-Key": api_key},
        json={"nonce": args.nonce},
        timeout=10,
    )
    if response.status_code >= 400:
        print(response.text, file=sys.stderr)
        return 1
    print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
