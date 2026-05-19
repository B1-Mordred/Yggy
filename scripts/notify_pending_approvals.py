#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from registry_lib import ROOT, RegistryError, api_key_from_env, api_request, load_local_env

APPROVAL_TARGET = "approvals"
MAX_APPROVALS = 25
MAX_ACTIONS = 8
MAX_DIFF_ITEMS = 8
MAX_TEXT = 300


RequestFn = Callable[..., Any]


def selected_pending_approvals(
    status_payload: dict[str, Any],
    *,
    approval_id: str | None = None,
) -> list[dict[str, Any]]:
    approvals = status_payload.get("pending_approvals")
    if not isinstance(approvals, list):
        return []
    selected = [approval for approval in approvals if isinstance(approval, dict)]
    if approval_id:
        selected = [approval for approval in selected if approval.get("id") == approval_id]
    return selected[:MAX_APPROVALS]


def render_approval_notification(approval: dict[str, Any]) -> str:
    task = approval.get("task") if isinstance(approval.get("task"), dict) else {}
    review = approval.get("review") if isinstance(approval.get("review"), dict) else {}
    config_diff = review.get("config_diff") if isinstance(review.get("config_diff"), dict) else {}
    diff = config_diff.get("diff") if isinstance(config_diff.get("diff"), dict) else {}
    output = task.get("output") if isinstance(task.get("output"), dict) else {}
    runtime = task.get("runtime") if isinstance(task.get("runtime"), dict) else {}
    trigger = task.get("trigger") if isinstance(task.get("trigger"), dict) else {}

    lines = [
        "**Approval requested**",
        "",
        f"Task: `{_text(approval.get('task_id'))}`",
        f"Task name: {_text(task.get('name') or 'unknown')}",
        f"Task type: `{_text(task.get('type') or 'unknown')}`",
        f"Approval level: `{_text(approval.get('approval_level'))}`",
        f"Requested by: `{_text(approval.get('requested_by'))}`",
        f"Requested at: `{_text(approval.get('created_at'))}`",
        f"Risk: `{_text(approval.get('risk'))}`",
        f"Status: `{_text(approval.get('status'))}`",
        "",
        "Purpose:",
        _text(approval.get("summary") or "No summary recorded."),
        "",
        "Actions:",
    ]
    actions = review.get("actions") if isinstance(review.get("actions"), list) else []
    if actions:
        lines.extend(f"- {_text(action)}" for action in actions[:MAX_ACTIONS])
    else:
        lines.append("- Review the task configuration in the local operations UI.")

    lines.extend(
        [
            "",
            "Worst-case failure mode:",
            _text(review.get("failure_mode") or "The task could produce incorrect output or fail within its configured policy."),
            "",
            "Config review:",
            f"- Enabled now: `{str(task.get('enabled')).lower()}`",
            f"- Trigger: `{_text(trigger.get('cron', 'n/a'))}` `{_text(trigger.get('timezone', 'n/a'))}`",
            f"- Output: `{_text(output.get('channel', 'n/a'))}` target `{_text(output.get('target', 'n/a'))}`",
            f"- Dry run: `{str(runtime.get('dry_run', True)).lower()}`",
            f"- Config version: `{_text(config_diff.get('version', 'n/a'))}` `{_text(config_diff.get('change_type', 'n/a'))}`",
            f"- Diff: {_diff_counts_text(diff)}",
        ]
    )
    lines.extend(_diff_item_lines(diff))
    lines.extend(
        [
            "",
            f"Approval ID: `{_text(approval.get('id'))}`",
            "Nonce: not included in Discord notifications.",
            "",
            "Approve only through the local approval CLI/UI. Do not paste admin secrets or approval nonces into chat.",
        ]
    )
    return "\n".join(lines)[:2000]


def send_approval_notifications(
    *,
    base_url: str,
    api_key: str,
    dry_run: bool,
    approval_id: str | None = None,
    all_pending: bool = False,
    target: str = APPROVAL_TARGET,
    request_fn: RequestFn = api_request,
) -> dict[str, Any]:
    if target != APPROVAL_TARGET:
        raise ValueError("approval notifications may only target the whitelisted approvals channel")
    if not dry_run and not approval_id and not all_pending:
        raise ValueError("live Discord sends require --approval-id or --all")

    status_payload = request_fn("GET", "/ops/status", base_url=base_url, api_key=api_key)
    approvals = selected_pending_approvals(status_payload, approval_id=approval_id)
    results: list[dict[str, Any]] = []
    for approval in approvals:
        content = render_approval_notification(approval)
        response = request_fn(
            "POST",
            "/notifications/discord/send",
            base_url=base_url,
            api_key=api_key,
            payload={"target": target, "content": content, "dry_run": dry_run},
        )
        results.append(
            {
                "approval_id": approval.get("id"),
                "task_id": approval.get("task_id"),
                "dry_run": dry_run,
                "message_chars": len(content),
                "notification": response,
            }
        )
    return {
        "pending_count": len(status_payload.get("pending_approvals") or []),
        "selected_count": len(approvals),
        "sent_count": len(results),
        "dry_run": dry_run,
        "target": target,
        "results": results,
    }


def _diff_counts_text(diff: dict[str, Any]) -> str:
    counts = diff.get("counts") if isinstance(diff.get("counts"), dict) else {}
    return (
        f"added `{counts.get('added', 0)}`, "
        f"removed `{counts.get('removed', 0)}`, "
        f"changed `{counts.get('changed', 0)}`"
        f"{'; truncated' if diff.get('truncated') else ''}"
    )


def _diff_item_lines(diff: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for kind in ("added", "removed", "changed"):
        items = diff.get(kind) if isinstance(diff.get(kind), list) else []
        for item in items[:MAX_DIFF_ITEMS]:
            if not isinstance(item, dict):
                continue
            path = _text(item.get("path", "$"))
            if kind == "added":
                rows.append(f"- Added `{path}`")
            elif kind == "removed":
                rows.append(f"- Removed `{path}`")
            else:
                rows.append(f"- Changed `{path}`")
            if len(rows) >= MAX_DIFF_ITEMS:
                return rows
    return rows


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True, default=str)
    else:
        text = str(value)
    text = text.replace("\n", " ").strip()
    if len(text) > limit:
        return f"{text[:limit]}...<truncated>"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Notify Discord about pending Yggy approvals without approving them.")
    parser.add_argument("--base-url", default=os.getenv("AUTOMATION_API_BASE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--api-key-env", default="AUTOMATION_ADMIN_API_KEY")
    parser.add_argument("--approval-id", help="Notify for one pending approval id")
    parser.add_argument("--all", action="store_true", help="Notify for all pending approvals")
    parser.add_argument("--dry-run", action="store_true", help="Do not send a live Discord message")
    parser.add_argument("--target", default=APPROVAL_TARGET, help="Must remain approvals")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    load_local_env(ROOT)
    try:
        result = send_approval_notifications(
            base_url=args.base_url,
            api_key=api_key_from_env(args.api_key_env),
            dry_run=args.dry_run,
            approval_id=args.approval_id,
            all_pending=args.all,
            target=args.target,
        )
    except (RegistryError, ValueError) as exc:
        print(f"Approval notification failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif result["selected_count"] == 0:
        print("No pending approvals matched the selection.")
    else:
        mode = "dry-run" if result["dry_run"] else "sent"
        print(f"Approval notifications {mode}: {result['sent_count']} to target `{result['target']}`")
        for item in result["results"]:
            print(f"- approval {item['approval_id']} task {item['task_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
