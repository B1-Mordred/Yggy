#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from registry_lib import (
    ROOT,
    api_key_from_env,
    api_request,
    ensure_import_paths,
    fetch_live_tasks,
    load_local_env,
    load_yaml_registry,
    validate_task_against_policy,
)


def disabled_draft_payload(config: dict[str, Any]) -> dict[str, Any]:
    payload = validate_task_against_policy(config)
    payload["enabled"] = False
    return payload


def import_task_drafts(
    *,
    base_url: str,
    api_key: str,
    config_dir: Path,
    task_id: str | None = None,
    update_existing: bool = False,
    request_approval: bool = False,
    print_nonces: bool = False,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    ensure_import_paths()
    if request_approval and not print_nonces:
        raise ValueError("--request-approval requires --print-nonces so the local operator can approve with the nonce")
    local_tasks = load_yaml_registry(config_dir / "tasks")
    selected_ids = [task_id] if task_id else sorted(local_tasks)
    live_tasks = fetch_live_tasks(base_url, api_key) if api_key != "dry-run" else {}
    actions: list[dict[str, Any]] = []

    for selected_id in selected_ids:
        if selected_id not in local_tasks:
            raise ValueError(f"task YAML not found: {selected_id}")
        payload = disabled_draft_payload(local_tasks[selected_id])
        exists = selected_id in live_tasks
        action: dict[str, Any] = {
            "task_id": selected_id,
            "exists": exists,
            "queued_approval_request": False,
            "approval_id": None,
            "nonce": None,
            "nonce_omitted": False,
            "initial_approval_rejected": False,
        }
        if exists and not update_existing:
            action["action"] = "skipped_existing"
            actions.append(action)
            continue
        if dry_run:
            action["action"] = "would_update_existing" if exists else "would_create_draft"
            actions.append(action)
            continue

        if exists:
            api_request("PUT", f"/tasks/{selected_id}", base_url=base_url, api_key=api_key, payload=payload)
            action["action"] = "updated_disabled_draft"
        else:
            response = api_request("POST", "/tasks/draft", base_url=base_url, api_key=api_key, payload=payload)
            approval = response.get("approval") if isinstance(response, dict) else None
            if isinstance(approval, dict):
                action["approval_id"] = approval.get("id")
                if print_nonces:
                    action["nonce"] = approval.get("nonce")
                else:
                    action["nonce_omitted"] = bool(approval.get("nonce"))
                    if approval.get("id"):
                        api_request("POST", f"/approvals/{approval['id']}/reject", base_url=base_url, api_key=api_key)
                        api_request("PUT", f"/tasks/{selected_id}", base_url=base_url, api_key=api_key, payload=payload)
                        action["initial_approval_rejected"] = True
            action["action"] = "created_disabled_draft"

        if request_approval and exists:
            response = api_request("POST", f"/tasks/{selected_id}/request-approval", base_url=base_url, api_key=api_key)
            if isinstance(response, dict):
                action["queued_approval_request"] = True
                action["approval_id"] = response.get("id")
                action["nonce"] = response.get("nonce")
        actions.append(action)

    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="Import YAML tasks as disabled drafts through the automation API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    parser.add_argument("--api-key-env", default="AUTOMATION_ADMIN_API_KEY")
    parser.add_argument("--task-id", help="Import only one task id")
    parser.add_argument("--update-existing", action="store_true", help="Update existing tasks as disabled drafts")
    parser.add_argument("--request-approval", action="store_true", help="Request approval after updating an existing task")
    parser.add_argument("--print-nonces", action="store_true", help="Print local approval nonces. Never paste them into chat.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print intended actions without calling the API")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    load_local_env()
    try:
        api_key = api_key_from_env(args.api_key_env) if not args.dry_run else os.getenv(args.api_key_env, "dry-run")
        actions = import_task_drafts(
            base_url=args.base_url,
            api_key=api_key,
            config_dir=args.config_dir,
            task_id=args.task_id,
            update_existing=args.update_existing,
            request_approval=args.request_approval,
            print_nonces=args.print_nonces,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(actions, indent=2))
    else:
        for action in actions:
            line = f"{action['task_id']}: {action['action']}"
            if action.get("approval_id"):
                line += f" approval_id={action['approval_id']}"
            if action.get("nonce"):
                line += f" nonce={action['nonce']}"
            if action.get("nonce_omitted"):
                line += " nonce=omitted"
            if action.get("initial_approval_rejected"):
                line += " initial_approval=rejected"
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
