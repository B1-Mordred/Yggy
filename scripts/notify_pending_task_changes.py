#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

from registry_lib import RegistryError, api_key_from_env, api_request, load_local_env

APPROVAL_TARGET = "approvals"
MAX_PROPOSALS = 25
MAX_DIFF_ITEMS = 10
MAX_TEXT = 300

RequestFn = Callable[..., Any]


def selected_task_change_proposals(
    proposals_payload: Any,
    *,
    proposal_id: str | None = None,
) -> list[dict[str, Any]]:
    if isinstance(proposals_payload, list):
        proposals = proposals_payload
    elif isinstance(proposals_payload, dict) and isinstance(proposals_payload.get("proposals"), list):
        proposals = proposals_payload["proposals"]
    else:
        proposals = []
    selected = [proposal for proposal in proposals if isinstance(proposal, dict)]
    if proposal_id:
        selected = [proposal for proposal in selected if proposal.get("id") == proposal_id]
    return selected[:MAX_PROPOSALS]


def render_task_change_notification(proposal: dict[str, Any]) -> str:
    risk = proposal.get("risk") if isinstance(proposal.get("risk"), dict) else {}
    diff = proposal.get("diff") if isinstance(proposal.get("diff"), dict) else {}
    categories = risk.get("categories") if isinstance(risk.get("categories"), dict) else {}
    lines = [
        "**Task change proposal pending**",
        "",
        f"Proposal: `{_text(proposal.get('id'))}`",
        f"Task: `{_text(proposal.get('task_id'))}`",
        f"Status: `{_text(proposal.get('status'))}`",
        f"Approval level: `{_text(proposal.get('approval_level'))}`",
        f"Requested by: `{_text(proposal.get('requested_by'))}`",
        f"Requested at: `{_text(proposal.get('created_at'))}`",
        f"Risk: `{_text(risk.get('severity', 'n/a'))}`",
        "",
        "Summary:",
        _text(proposal.get("summary") or "No summary recorded."),
        "",
        "Diff:",
        f"- {_diff_counts_text(diff)}",
    ]
    if categories:
        for category, paths in categories.items():
            lines.append(f"- {_text(category)}: {_text(paths)}")
    lines.extend(_diff_item_lines(diff))
    lines.extend(
        [
            "",
            "Nonce: not included in Discord notifications.",
            "",
            "Approve only through the local /ops UI or task-change CLI. Do not paste admin secrets or proposal nonces into chat.",
        ]
    )
    return "\n".join(lines)[:2000]


def send_task_change_notifications(
    *,
    base_url: str,
    api_key: str,
    dry_run: bool,
    proposal_id: str | None = None,
    all_pending: bool = False,
    include_approved: bool = False,
    target: str = APPROVAL_TARGET,
    request_fn: RequestFn = api_request,
) -> dict[str, Any]:
    if target != APPROVAL_TARGET:
        raise ValueError("task change notifications may only target the whitelisted approvals channel")
    if not dry_run and not proposal_id and not all_pending:
        raise ValueError("live Discord sends require --proposal-id or --all")

    if proposal_id:
        proposals_payload: Any = [request_fn("GET", f"/task-change-proposals/{proposal_id}", base_url=base_url, api_key=api_key)]
    else:
        statuses = ["pending", "approved"] if include_approved else ["pending"]
        proposals: list[dict[str, Any]] = []
        for status in statuses:
            payload = request_fn(
                "GET",
                f"/task-change-proposals?status={status}&limit={MAX_PROPOSALS}",
                base_url=base_url,
                api_key=api_key,
            )
            proposals.extend(selected_task_change_proposals(payload))
        proposals_payload = proposals

    selected = selected_task_change_proposals(proposals_payload, proposal_id=proposal_id)
    results: list[dict[str, Any]] = []
    for proposal in selected:
        content = render_task_change_notification(proposal)
        response = request_fn(
            "POST",
            "/notifications/discord/send",
            base_url=base_url,
            api_key=api_key,
            payload={"target": target, "content": content, "dry_run": dry_run},
        )
        results.append(
            {
                "proposal_id": proposal.get("id"),
                "task_id": proposal.get("task_id"),
                "dry_run": dry_run,
                "message_chars": len(content),
                "notification": response,
            }
        )
    return {
        "selected_count": len(selected),
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
    parser = argparse.ArgumentParser(description="Notify Discord about pending task-change proposals without approving them.")
    parser.add_argument("--base-url", default=os.getenv("AUTOMATION_API_BASE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--api-key-env", default="AUTOMATION_ADMIN_API_KEY")
    parser.add_argument("--proposal-id", help="Notify for one task-change proposal id")
    parser.add_argument("--all", action="store_true", help="Notify for all pending task-change proposals")
    parser.add_argument("--include-approved", action="store_true", help="Also notify for approved proposals waiting to be applied")
    parser.add_argument("--dry-run", action="store_true", help="Do not send a live Discord message")
    parser.add_argument("--target", default=APPROVAL_TARGET, help="Must remain approvals")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    load_local_env(ROOT)
    try:
        result = send_task_change_notifications(
            base_url=args.base_url,
            api_key=api_key_from_env(args.api_key_env),
            dry_run=args.dry_run,
            proposal_id=args.proposal_id,
            all_pending=args.all,
            include_approved=args.include_approved,
            target=args.target,
        )
    except (RegistryError, ValueError) as exc:
        print(f"Task change notification failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif result["selected_count"] == 0:
        print("No task-change proposals matched the selection.")
    else:
        mode = "dry-run" if result["dry_run"] else "sent"
        print(f"Task change notifications {mode}: {result['sent_count']} to target `{result['target']}`")
        for item in result["results"]:
            print(f"- proposal {item['proposal_id']} task {item['task_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
