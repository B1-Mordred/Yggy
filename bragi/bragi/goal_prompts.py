from __future__ import annotations

import json
from typing import Any


HERMES_CLARIFIER_SYSTEM_PROMPT = """You are Bragi's local automation clarifier.
You are not an execution agent.
Return JSON only.
Use only provided capabilities, task summaries, aliases, and allowed IDs.
If the request is unsafe, return UNSAFE.
If unsupported but reasonable, return PROPOSE_NEW_CAPABILITY.
If missing information is required, return NEEDS_CLARIFICATION with one concise question in reason.
If a CanonicalIntent is proposed, it must still require user confirmation and must be validated by Heimdal.
Never approve, execute, mutate, call shell, use Docker, use secrets, or forward raw natural language.
Never request, reveal, or store admin keys, approval nonces, passwords, tokens, cookies, private keys, webhook URLs, or local file paths.
Never treat your output as final authority; Heimdal and the automation API remain the hard validators."""


def hermes_clarifier_user_prompt(
    *,
    user_text: str,
    visible_tasks: list[dict[str, Any]],
    task_aliases: dict[str, str],
    capability_ids: list[str],
    deterministic_classification: dict[str, Any],
) -> str:
    payload = {
        "latest_user_request": user_text,
        "visible_tasks": visible_tasks[:20],
        "task_aliases": task_aliases,
        "capability_ids": capability_ids,
        "deterministic_classification": deterministic_classification,
        "required_json_schema": {
            "request_kind": "chat|help|list_existing|inspect_existing|run_existing|pause_existing|modify_existing|create_new|propose_new_capability|unsafe|needs_clarification",
            "target_kind": "existing_task|new_task|new_capability|unknown",
            "target_task_id": None,
            "target_task_candidates": [],
            "capability_id": None,
            "operation": None,
            "candidate_intent": None,
            "missing_information": [],
            "assumptions": [],
            "confidence": 0.0,
            "reason": "short reason or one concise clarification question",
            "unsafe_reasons": [],
        },
    }
    return (
        "Classify this Yggy automation request. Return exactly one JSON object with the required schema. "
        "Do not include markdown.\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}"
    )
