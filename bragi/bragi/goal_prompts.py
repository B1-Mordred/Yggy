from __future__ import annotations

import json
from typing import Any


HERMES_CLARIFIER_SYSTEM_PROMPT = """You are Bragi's local automation clarifier.
You are not an execution agent.
Return JSON only.
Use only provided capabilities, task summaries, aliases, and allowed IDs.
Map monitoring the AI stack, Open WebUI, Ollama, Yggy, workers, or n8n to server_health.v1 when that capability is available.
Map briefings, newsletters, digests, morning/evening summaries, or news summaries to topic_digest.v1 when that capability is available.
Map changes to an existing digest's sources, subjects, filters, or output target to topic_digest.modify_subjects.v1.
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
        "canonical_intent_shapes": {
            "draft_task": {
                "intent": "draft_task",
                "capability_id": "server_health.v1|topic_digest.v1|printer_supply_status.v1|n8n_webhook.v1",
                "confidence": 0.75,
                "requires_user_confirmation": True,
                "user_confirmation_obtained": False,
                "slots": {},
            },
            "propose_task_change": {
                "intent": "propose_task_change",
                "capability_id": "topic_digest.modify_subjects.v1",
                "confidence": 0.75,
                "requires_user_confirmation": True,
                "user_confirmation_obtained": False,
                "slots": {"task_id": "existing_task_id"},
            },
        },
        "examples": [
            {
                "request": "set up an AI stack monitor every morning",
                "classification": {
                    "request_kind": "create_new",
                    "target_kind": "new_task",
                    "capability_id": "server_health.v1",
                    "operation": {"intent": "draft_task", "capability_id": "server_health.v1"},
                    "candidate_intent": {
                        "intent": "draft_task",
                        "capability_id": "server_health.v1",
                        "confidence": 0.78,
                        "requires_user_confirmation": True,
                        "user_confirmation_obtained": False,
                        "slots": {
                            "task_id": "daily_ai_stack_health",
                            "name": "Daily AI Stack Health Check",
                            "cron": "0 8 * * *",
                            "timezone": "Europe/Berlin",
                            "check_ids": ["open_webui", "ollama", "automation_api", "automation_worker", "n8n"],
                            "output_target": "alerts",
                        },
                    },
                    "missing_information": [],
                    "confidence": 0.78,
                    "reason": "AI stack monitoring maps to the registered server_health.v1 capability.",
                    "unsafe_reasons": [],
                },
            },
            {
                "request": "send daily brief now",
                "classification": {
                    "request_kind": "run_existing",
                    "target_kind": "existing_task",
                    "target_task_id": "daily_local_ai_security_briefing",
                    "operation": {"action": "run_task", "task_id": "daily_local_ai_security_briefing"},
                    "confidence": 0.9,
                    "reason": "The request runs an existing known task.",
                },
            },
        ],
    }
    return (
        "Classify this Yggy automation request. Return exactly one JSON object with the required schema. "
        "Do not include markdown.\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}"
    )
