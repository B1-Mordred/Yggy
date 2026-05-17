from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .memory_store import (
    ALLOWED_MEMORY_CATEGORIES,
    MemoryValidationError,
    commit_memory,
    forget_memory,
    memory_store_status,
    propose_memory,
    query_memory,
    safe_identifier,
)

MODEL_ID = os.getenv("BRAGI_MODEL_ID", "bragi")
DISPLAY_NAME = "Bragi"
API_KEY = os.getenv("BRAGI_API_KEY", "").strip()
DEFAULT_USER_ID = os.getenv("BRAGI_DEFAULT_USER_ID", "local_user").strip() or "local_user"
AUTOMATION_API_BASE_URL = os.getenv("AUTOMATION_API_BASE_URL", "http://automation-api:8088").rstrip("/")
AUTOMATION_TOOL_API_KEY = os.getenv("AUTOMATION_TOOL_API_KEY", "").strip()
YGGDRASIL_BASE_URL = os.getenv("YGGDRASIL_BASE_URL", "http://host.docker.internal:8642").rstrip("/")
YGGDRASIL_API_KEY = os.getenv("BRAGI_YGGDRASIL_API_KEY", os.getenv("API_SERVER_KEY", "")).strip()
HTTP_TIMEOUT = int(os.getenv("BRAGI_HTTP_TIMEOUT", "30"))
GENERAL_CHAT_ENABLED = os.getenv("BRAGI_GENERAL_CHAT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
CHAT_MODEL = os.getenv("BRAGI_CHAT_MODEL", os.getenv("LLM_SUMMARIZER_MODEL", "llama3.1:8b")).strip()
CHAT_TEMPERATURE = float(os.getenv("BRAGI_CHAT_TEMPERATURE", "0.55"))
CHAT_TIMEOUT = float(os.getenv("BRAGI_CHAT_TIMEOUT", "30"))
CHAT_NUM_CTX = int(os.getenv("BRAGI_CHAT_NUM_CTX", "4096"))
CHAT_MAX_TOKENS = int(os.getenv("BRAGI_CHAT_MAX_TOKENS", "512"))
MEMORY_FILE = os.getenv("BRAGI_MEMORY_FILE", "/app/configs/bragi/memory.yaml").strip()
CONFIG_ROOT = os.getenv("BRAGI_CONFIG_ROOT", "/app/configs").strip()
CONTEXT_CATEGORIES = {
    "tasks",
    "pending_reviews",
    "capabilities",
    "sources",
    "health_checks",
    "n8n_webhooks",
    "service_status",
    "recent_runs",
    "memory",
}
TASK_ALIASES = {
    "daily brief": "daily_local_ai_security_briefing",
    "daily briefing": "daily_local_ai_security_briefing",
    "daily security brief": "daily_local_ai_security_briefing",
    "daily security briefing": "daily_local_ai_security_briefing",
    "local ai brief": "daily_local_ai_security_briefing",
    "local ai briefing": "daily_local_ai_security_briefing",
    "local ai security briefing": "daily_local_ai_security_briefing",
    "daily local ai security briefing": "daily_local_ai_security_briefing",
    "server health": "morning_server_health_check",
    "server health check": "morning_server_health_check",
    "morning server health": "morning_server_health_check",
    "morning server health check": "morning_server_health_check",
    "backup verification": "yggy_backup_verification",
    "backup check": "yggy_backup_verification",
    "backup health": "yggy_backup_verification",
    "backups": "yggy_backup_verification",
}
SOURCE_ALIASES = {
    "open webui": "open_webui_releases",
    "open-webui": "open_webui_releases",
    "ollama": "ollama_releases",
    "n8n": "n8n_releases",
    "docker": "docker_blog",
    "docker blog": "docker_blog",
}
CHECK_ALIASES = {
    "open webui": "open_webui",
    "open-webui": "open_webui",
    "ollama": "ollama",
    "automation api": "automation_api",
    "yggy api": "automation_api",
    "yggy automation-api": "automation_api",
    "worker": "automation_worker",
    "automation worker": "automation_worker",
    "yggdrasil": "yggdrasil_action_api",
    "n8n": "n8n",
}
GENERAL_CHAT_SYSTEM_PROMPT = """You are Bragi, the user's natural human-facing AI concierge.

Speak naturally and helpfully. You may have a restrained Norse-skald flavor, dry wit, and occasional dark humor when it fits, but do not overdo it.

You have no tools in this general-chat fallback. Do not claim that you executed work, changed configurations, approved anything, contacted Yggdrasil, sent Discord messages, accessed files, or talked to external services. If the user asks for an automation, approval, or execution, explain the concept conversationally; the outer Bragi gateway will handle registered automation capabilities separately.

System context for conversational help: the old Hermes brief-management route is retired. Briefs and digests now belong to Yggy `topic_digest` automations. If the user asks how to add or change a subject/topic, tell them conversationally to describe the desired topic, sources, filters, schedule, and Discord target; if they ask for an actual change, the outer gateway can route a supported request for confirmation and Yggy approval. Do not invent UI buttons, menus, or a "Briefs section" unless the user provided that context.

Do not ask for or reveal secrets, tokens, passwords, cookies, private keys, approval nonces, or webhook URLs."""

app = FastAPI(
    title="Bragi Natural Agent",
    version="0.1.0",
    description="Natural human-facing concierge for Yggy. Bragi talks; Heimdal validates; Yggdrasil compiles.",
)


class RouteDiagnosticsRequest(BaseModel):
    text: str | None = Field(default=None, max_length=12000)
    messages: list[dict[str, Any]] | None = None


class ContextQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=12000)
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=10, ge=1, le=50)


class MemoryQueryRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    include_pending: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class MemoryProposeRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=128)
    scope: str = Field(default="user", max_length=32)
    category: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=1, max_length=128)
    value: Any
    source: str = Field(default="explicit_user_instruction", max_length=128)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class MemoryCommitRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=128)
    memory_id: str = Field(min_length=1, max_length=64)


class MemoryForgetRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=128)
    memory_id: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    key: str | None = Field(default=None, max_length=128)
    search: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=50, ge=1, le=100)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    parts.append(value)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content or "")


def latest_user_request(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return extract_text(message.get("content")).strip()
    return ""


def prior_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(extract_text(message.get("content")) for message in messages[:-1])


def authorized(authorization: str | None) -> bool:
    if not API_KEY:
        return True
    return authorization == f"Bearer {API_KEY}"


def api_request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not AUTOMATION_TOOL_API_KEY:
        return {"outcome": "REJECT_UNSAFE", "message": "AUTOMATION_TOOL_API_KEY is not configured for Bragi."}
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.request(
            method,
            f"{AUTOMATION_API_BASE_URL}{path}",
            headers={"X-Automation-Api-Key": AUTOMATION_TOOL_API_KEY},
            json=payload,
        )
    if response.status_code >= 400:
        return {"outcome": "REJECT_UNSAFE", "message": f"automation API returned {response.status_code}", "detail": response.text}
    data = response.json() if response.content else {}
    return data if isinstance(data, dict) else {"data": data}


def yggdrasil_canonical_request(payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if YGGDRASIL_API_KEY:
        headers["Authorization"] = f"Bearer {YGGDRASIL_API_KEY}"
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.post(f"{YGGDRASIL_BASE_URL}/v1/yggdrasil/canonical-actions", headers=headers, json=payload)
    if response.status_code in {401, 403}:
        return {
            "status": "unauthorized",
            "answer": (
                "I understood the automation request, but this Bragi instance is not authorized to talk to Yggdrasil. "
                "Nothing was approved or executed."
            ),
        }
    if response.status_code >= 400:
        return {"status": "error", "answer": f"Yggdrasil rejected the canonical action with HTTP {response.status_code}: {response.text}"}
    data = response.json() if response.content else {}
    return data if isinstance(data, dict) else {"status": "error", "answer": "Yggdrasil returned a non-object response."}


def has_secret_like_material(value: Any) -> bool:
    text = json.dumps(value, default=str).lower() if not isinstance(value, str) else value.lower()
    secret_words = ("api_key", "apikey", "token", "password", "secret", "webhook_url", "private_key", "cookie", "nonce")
    return any(word in text for word in secret_words)


def load_memory() -> dict[str, Any]:
    if not MEMORY_FILE:
        return {}
    path = Path(MEMORY_FILE)
    try:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"bragi memory load failed: {exc}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    if has_secret_like_material(data):
        print("bragi memory ignored because it contains secret-like keys or values", file=sys.stderr)
        return {}
    return data


def static_memory_payload() -> dict[str, Any]:
    memory = load_memory()
    allowed = {
        "preferred_language",
        "message_style",
        "default_timezone",
        "default_schedule",
        "default_output_target",
        "default_dry_run",
        "service_aliases",
        "automation_preferences",
        "notes",
    }
    return {key: value for key, value in memory.items() if key in allowed}


def persistent_memory_payload(user_id: str = DEFAULT_USER_ID, *, include_pending: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    try:
        return [
            context_redact(record)
            for record in query_memory(user_id=user_id, include_pending=include_pending, limit=limit)
        ]
    except Exception as exc:
        print(f"bragi persistent memory load failed: {exc}", file=sys.stderr)
        return []


def memory_context(user_id: str = DEFAULT_USER_ID) -> str:
    payload = {
        "static": static_memory_payload(),
        "records": persistent_memory_payload(user_id=user_id, include_pending=False, limit=20),
    }
    if not payload["static"] and not payload["records"]:
        return ""
    rendered = yaml.safe_dump(payload, sort_keys=True, allow_unicode=False)
    return rendered[:3000]


def memory_summary(user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    return context_redact(
        {
            "user_id": user_id,
            "static": static_memory_payload(),
            "records": persistent_memory_payload(user_id=user_id, include_pending=False, limit=50),
        }
    )


def config_path(relative: str) -> Path:
    candidate = Path(CONFIG_ROOT) / relative
    if candidate.exists():
        return candidate
    return Path.cwd() / "configs" / relative


def read_yaml_registry(relative: str, collection_key: str) -> list[dict[str, Any]]:
    path = config_path(relative)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"bragi context registry load failed for {relative}: {exc}", file=sys.stderr)
        return []
    items = data.get(collection_key) if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [context_redact(item) for item in items if isinstance(item, dict)]


def context_redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(marker in lowered for marker in ("authorization", "cookie", "password", "secret", "token", "api_key", "apikey", "private_key", "credential", "nonce")):
                redacted[key_text] = "[redacted]"
                continue
            if lowered in {"url", "webhook_url", "path"}:
                redacted[key_text] = "[omitted]"
                continue
            redacted[key_text] = context_redact(item, depth=depth + 1)
        return redacted
    if isinstance(value, list):
        return [context_redact(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        text = redact_diagnostic_text(value)
        if has_secret_like_material(text):
            return "[redacted]"
        return text[:1000]
    return value


def context_api_get(path: str) -> Any:
    response = api_request("GET", path)
    if "data" in response and set(response) == {"data"}:
        return response["data"]
    return response


def safe_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    config = task.get("config") if isinstance(task.get("config"), dict) else {}
    trigger = config.get("trigger") if isinstance(config.get("trigger"), dict) else {}
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    return context_redact(
        {
            "id": task.get("id"),
            "name": task.get("name"),
            "type": task.get("type"),
            "enabled": task.get("enabled"),
            "status": task.get("status"),
            "approval_level": task.get("approval_level"),
            "created_by": task.get("created_by"),
            "trigger": {
                "kind": trigger.get("kind"),
                "cron": trigger.get("cron"),
                "timezone": trigger.get("timezone"),
            },
            "output": {
                "channel": output.get("channel"),
                "target": output.get("target"),
            },
            "dry_run": runtime.get("dry_run"),
        }
    )


def safe_run_summary(run: dict[str, Any]) -> dict[str, Any]:
    log = run.get("log") if isinstance(run.get("log"), dict) else {}
    result_status = log.get("result_status") or log.get("status")
    notification = log.get("notification") if isinstance(log.get("notification"), dict) else {}
    return context_redact(
        {
            "id": run.get("id"),
            "task_id": run.get("task_id"),
            "status": run.get("status"),
            "created_at": run.get("created_at"),
            "completed_at": run.get("completed_at"),
            "result_status": result_status,
            "notification_sent": notification.get("sent") if notification else None,
        }
    )


def safe_capability_summary(capability: dict[str, Any]) -> dict[str, Any]:
    return context_redact(
        {
            "id": capability.get("id"),
            "purpose": capability.get("purpose"),
            "maps_to_task_type": capability.get("maps_to_task_type"),
            "allowed_approval_levels": capability.get("allowed_approval_levels", []),
            "allowed_output_targets": capability.get("allowed_output_targets", []),
            "required_slots": capability.get("required_slots", []),
            "allowed_source_ids": capability.get("allowed_source_ids", []),
            "allowed_check_ids": capability.get("allowed_check_ids", []),
            "allowed_webhook_ids": capability.get("allowed_webhook_ids", []),
            "safety_rules": capability.get("safety_rules", []),
        }
    )


def context_categories_for_text(text: str, requested_category: str | None = None) -> list[str]:
    if requested_category:
        category = requested_category.strip().lower()
        return [category] if category in CONTEXT_CATEGORIES else []
    lowered = text.lower()
    if re.match(r"^\s*(draft|create|set up|setup|schedule|run|send|pause|disable|approve|reject)\b", lowered):
        return []
    categories: list[str] = []

    def add(*items: str) -> None:
        for item in items:
            if item not in categories:
                categories.append(item)

    if "what can you automate" in lowered or "what can yggy automate" in lowered or "capabilit" in lowered or "supported automation" in lowered:
        add("capabilities", "sources", "health_checks", "n8n_webhooks")
    if "what does yggy know" in lowered or "what do you know about my ai stack" in lowered:
        add("tasks", "capabilities", "sources", "health_checks", "n8n_webhooks", "memory")
    if "source" in lowered or "rss" in lowered or "feed" in lowered:
        add("sources")
    if "health check" in lowered or "check ids" in lowered or "known services" in lowered or "service aliases" in lowered:
        add("health_checks")
    if "webhook" in lowered or "n8n workflow" in lowered:
        add("n8n_webhooks")
    if "pending" in lowered or "approval" in lowered or "review" in lowered:
        add("pending_reviews")
    if "live task" in lowered or "enabled task" in lowered or "draft task" in lowered or "task status" in lowered:
        add("tasks")
    if "recent run" in lowered or "run history" in lowered or "last run" in lowered:
        add("recent_runs")
    if "service status" in lowered or "control plane status" in lowered or "worker status" in lowered or "yggy status" in lowered:
        add("service_status")
    if "memory" in lowered or "preferences" in lowered or "remember" in lowered:
        add("memory")
    return categories


def build_context(query: str, *, user_id: str = DEFAULT_USER_ID, category: str | None = None, limit: int = 10) -> dict[str, Any]:
    categories = context_categories_for_text(query, category)
    if not categories:
        categories = ["tasks", "capabilities"]
    limit = max(1, min(limit, 50))
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    try:
        clean_user_id = safe_identifier(user_id, field_name="user_id")
    except MemoryValidationError:
        clean_user_id = DEFAULT_USER_ID

    def capture(name: str, producer) -> None:
        try:
            data[name] = producer()
        except Exception as exc:
            errors[name] = exc.__class__.__name__

    if "tasks" in categories or "pending_reviews" in categories:
        capture("tasks", lambda: [safe_task_summary(task) for task in context_api_get("/tasks")[:limit]])
    if "pending_reviews" in categories:
        def pending_reviews() -> list[dict[str, Any]]:
            tasks = data.get("tasks")
            if not isinstance(tasks, list):
                tasks = [safe_task_summary(task) for task in context_api_get("/tasks")[:limit]]
            return [task for task in tasks if task.get("status") == "pending_approval"]

        capture("pending_reviews", pending_reviews)
    if "capabilities" in categories:
        capture("capabilities", lambda: [safe_capability_summary(item) for item in context_api_get("/capabilities")[:limit]])
    if "sources" in categories:
        capture("sources", lambda: approved_source_summaries(limit=limit))
    if "health_checks" in categories:
        capture("health_checks", lambda: approved_health_check_summaries(limit=limit))
    if "n8n_webhooks" in categories:
        capture("n8n_webhooks", lambda: approved_webhook_summaries(limit=limit))
    if "service_status" in categories:
        capture("service_status", lambda: context_redact(context_api_get("/health")))
    if "recent_runs" in categories:
        capture("recent_runs", lambda: [safe_run_summary(run) for run in context_api_get(f"/runs?limit={limit}")])
    if "memory" in categories:
        capture("memory", lambda: memory_summary(clean_user_id))

    return {
        "service": "bragi",
        "context_version": 1,
        "read_only": True,
        "user_id": clean_user_id,
        "query_preview": redact_diagnostic_text(query)[:240],
        "categories": categories,
        "data": context_redact(data),
        "errors": errors,
        "redaction": {
            "raw_logs": "omitted",
            "approval_nonces": "omitted",
            "secrets": "redacted",
            "registry_urls": "omitted",
        },
    }


def approved_source_summaries(*, limit: int) -> list[dict[str, Any]]:
    sources = read_yaml_registry("sources/approved_sources.yaml", "sources")
    return [
        {
            "id": source.get("id"),
            "name": source.get("name"),
            "type": source.get("type"),
            "enabled": source.get("enabled"),
            "categories": source.get("categories", []),
            "trust_level": source.get("trust_level"),
            "max_items": source.get("max_items"),
        }
        for source in sources[:limit]
    ]


def approved_health_check_summaries(*, limit: int) -> list[dict[str, Any]]:
    services = read_yaml_registry("metrics/services.yaml", "services")
    return [
        {
            "id": service.get("id"),
            "name": service.get("name"),
            "type": service.get("type"),
            "enabled": service.get("enabled"),
            "expected_status": service.get("expected_status"),
            "description": service.get("description"),
        }
        for service in services[:limit]
    ]


def approved_webhook_summaries(*, limit: int) -> list[dict[str, Any]]:
    webhooks = read_yaml_registry("n8n/webhooks.yaml", "webhooks")
    return [
        {
            "id": webhook.get("id"),
            "name": webhook.get("name"),
            "method": webhook.get("method"),
            "enabled": webhook.get("enabled"),
            "max_payload_keys": webhook.get("max_payload_keys"),
            "description": webhook.get("description"),
        }
        for webhook in webhooks[:limit]
    ]


def format_context_answer(context: dict[str, Any]) -> str:
    data = context.get("data") if isinstance(context.get("data"), dict) else {}
    lines = ["Here is the read-only Yggy context I can see:"]
    tasks = data.get("tasks")
    if isinstance(tasks, list):
        enabled = [task for task in tasks if task.get("enabled")]
        pending = [task for task in tasks if task.get("status") == "pending_approval"]
        lines.extend(["", f"Tasks: {len(tasks)} visible, {len(enabled)} enabled, {len(pending)} pending approval."])
        for task in tasks[:10]:
            lines.append(
                f"- `{task.get('id')}`: {task.get('name')} ({task.get('type')}, status `{task.get('status')}`, enabled `{str(task.get('enabled')).lower()}`)"
            )
    pending_reviews = data.get("pending_reviews")
    if isinstance(pending_reviews, list):
        lines.extend(["", f"Pending reviews: {len(pending_reviews)}."])
        if pending_reviews:
            for task in pending_reviews[:10]:
                lines.append(f"- `{task.get('id')}`: {task.get('name')} ({task.get('approval_level')})")
        else:
            lines.append("- None.")
        lines.append("Approval nonces are not available to Bragi. Use the local ops UI or admin CLI for decisions.")
    capabilities = data.get("capabilities")
    if isinstance(capabilities, list):
        lines.extend(["", "Supported capabilities:"])
        for capability in capabilities[:10]:
            lines.append(f"- `{capability.get('id')}`: {capability.get('purpose')}")
    sources = data.get("sources")
    if isinstance(sources, list):
        lines.extend(["", "Approved sources:"])
        for source in sources[:10]:
            categories = ", ".join(source.get("categories") or [])
            lines.append(f"- `{source.get('id')}`: {source.get('name')} ({source.get('type')}, {source.get('trust_level')}, {categories})")
    checks = data.get("health_checks")
    if isinstance(checks, list):
        lines.extend(["", "Approved health checks:"])
        for check in checks[:10]:
            lines.append(f"- `{check.get('id')}`: {check.get('name')} ({check.get('type')})")
    webhooks = data.get("n8n_webhooks")
    if isinstance(webhooks, list):
        lines.extend(["", "Approved n8n webhooks:"])
        for webhook in webhooks[:10]:
            lines.append(f"- `{webhook.get('id')}`: {webhook.get('name')} ({webhook.get('method')})")
    service_status = data.get("service_status")
    if isinstance(service_status, dict):
        worker = service_status.get("worker") if isinstance(service_status.get("worker"), dict) else {}
        database = service_status.get("database") if isinstance(service_status.get("database"), dict) else {}
        lines.extend(
            [
                "",
                "Control-plane status:",
                f"- API: `{service_status.get('status')}`",
                f"- Database connected: `{str(database.get('connected')).lower()}`",
                f"- Worker: `{worker.get('status')}` age `{worker.get('age_seconds')}` seconds",
            ]
        )
    runs = data.get("recent_runs")
    if isinstance(runs, list):
        lines.extend(["", "Recent runs:"])
        if runs:
            for run in runs[:10]:
                lines.append(f"- `{run.get('id')}` task `{run.get('task_id')}` status `{run.get('status')}` completed `{run.get('completed_at')}`")
        else:
            lines.append("- None.")
    memory = data.get("memory")
    if isinstance(memory, dict):
        lines.extend(["", "Non-secret memory:"])
        if memory:
            lines.append(f"```yaml\n{yaml.safe_dump(memory, sort_keys=True, allow_unicode=False).strip()}\n```")
        else:
            lines.append("- No non-secret memory loaded.")
    errors = context.get("errors")
    if isinstance(errors, dict) and errors:
        lines.extend(["", "Some context could not be loaded:"])
        for key, value in errors.items():
            lines.append(f"- `{key}`: {value}")
    lines.append("")
    lines.append("This is context only. Changes, runs, and approvals still go through Heimdal, Yggdrasil, and the Yggy approval path.")
    return "\n".join(lines)


def memory_proposal_text(text: str) -> str | None:
    match = re.match(r"^\s*remember(?:\s+that)?\s+(.+?)\s*$", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    value = match.group(1).strip(" .")
    if not value or value.lower() in {"this", "it"}:
        return None
    return value[:1000]


def memory_forget_text(text: str) -> str | None:
    match = re.match(r"^\s*forget\s+(.+?)\s*$", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    value = match.group(1).strip(" .")
    return value or None


def is_memory_commit_confirmation(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text.strip().lower()).strip(" .!")
    return compact in {"remember", "save it", "save this", "commit memory", "yes remember"}


def memory_category_key_for_text(value: str) -> tuple[str, str]:
    lowered = value.lower()
    if "alert" in lowered or "notification" in lowered or "discord" in lowered:
        return "notification_style", "discord_notification_style"
    if "timezone" in lowered or "time zone" in lowered:
        return "default", "default_timezone"
    if "briefing target" in lowered or "brief target" in lowered or "default target" in lowered:
        return "default", "default_output_target"
    if "call" in lowered and re.search(r"\b(server|service|machine|box|host)\b", lowered):
        return "alias", slug(value[:80], "service_alias")
    if "prefer" in lowered or "like" in lowered:
        return "preference", slug(value[:80], "preference")
    if "interested in" in lowered or "care about" in lowered or "watch" in lowered:
        return "project_interest", slug(value[:80], "project_interest")
    return "note", slug(value[:80], "note")


def format_memory_record(record: dict[str, Any]) -> str:
    value = record.get("value")
    value_text = yaml.safe_dump(value, sort_keys=True, allow_unicode=False).strip() if not isinstance(value, str) else value
    return (
        f"- `{record.get('category')}.{record.get('key')}` = {value_text} "
        f"(status `{record.get('status')}`, source `{record.get('source')}`)"
    )


def format_memory_proposal(record: dict[str, Any]) -> str:
    return "\n".join(
        [
            "I can remember this as non-secret user context:",
            "",
            f"- User: `{record.get('user_id')}`",
            f"- Category: `{record.get('category')}`",
            f"- Key: `{record.get('key')}`",
            f"- Value: {record.get('value')}",
            "",
            "Reply `remember` to save it. This will not approve, enable, or run any automation.",
            "",
            "Pending memory proposal:",
            f"```json\n{json.dumps({'memory_action': 'propose', 'memory_id': record.get('id'), 'user_id': record.get('user_id')}, indent=2, sort_keys=True)}\n```",
        ]
    )


def pending_memory_from_prior(text: str) -> dict[str, Any] | None:
    matches = list(re.finditer(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL))
    for match in reversed(matches):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("memory_action") == "propose" and payload.get("memory_id"):
            return payload
    return None


def handle_memory_proposal(user_text: str, *, user_id: str = DEFAULT_USER_ID) -> str | None:
    value = memory_proposal_text(user_text)
    if value is None:
        return None
    category, key = memory_category_key_for_text(value)
    try:
        record = propose_memory(user_id=user_id, category=category, key=key, value=value)
    except MemoryValidationError as exc:
        return (
            f"I will not store that in memory: {exc}. "
            "Bragi memory is only for non-secret preferences, aliases, routines, and notes. "
            "Put credentials in `.env`, Docker secrets, n8n credentials, or a local secret manager."
        )
    except Exception as exc:
        return f"I could not create a memory proposal because the memory store returned `{exc.__class__.__name__}`."
    return format_memory_proposal(record)


def handle_memory_commit(prior: str) -> str | None:
    pending = pending_memory_from_prior(prior)
    if not pending:
        return None
    try:
        record = commit_memory(memory_id=str(pending["memory_id"]), user_id=str(pending.get("user_id") or DEFAULT_USER_ID))
    except MemoryValidationError as exc:
        return f"I could not save that memory: {exc}."
    except Exception as exc:
        return f"I could not save that memory because the memory store returned `{exc.__class__.__name__}`."
    return (
        "Saved as non-secret Bragi memory.\n\n"
        f"{format_memory_record(record)}\n\n"
        "This is conversation context only. Yggy policy still controls approvals, task state, and execution."
    )


def handle_memory_forget(user_text: str, *, user_id: str = DEFAULT_USER_ID) -> str | None:
    search = memory_forget_text(user_text)
    if search is None:
        return None
    if search.lower() in {"everything", "everything about me", "all memory", "all"}:
        search = None
    try:
        result = forget_memory(user_id=user_id, search=search)
    except MemoryValidationError as exc:
        return f"I could not forget that memory: {exc}."
    except Exception as exc:
        return f"I could not update memory because the memory store returned `{exc.__class__.__name__}`."
    records = result.get("records") if isinstance(result, dict) else []
    if not records:
        return "I did not find matching active Bragi memory to forget."
    lines = [f"Forgot {len(records)} Bragi memory record(s):"]
    lines.extend(format_memory_record(record) for record in records[:10])
    lines.append("")
    lines.append("This only changes Bragi memory. It does not change Yggy tasks, approvals, credentials, or run history.")
    return "\n".join(lines)


def format_memory_query_answer(user_id: str = DEFAULT_USER_ID) -> str:
    records = persistent_memory_payload(user_id=user_id, include_pending=False, limit=50)
    static = static_memory_payload()
    lines = ["Here is the non-secret Bragi memory I can use as conversation context:"]
    if static:
        lines.extend(["", "Static operator-curated memory:", f"```yaml\n{yaml.safe_dump(context_redact(static), sort_keys=True, allow_unicode=False).strip()}\n```"])
    if records:
        lines.extend(["", "User-scoped memory:"])
        lines.extend(format_memory_record(record) for record in records)
    if not static and not records:
        lines.append("")
        lines.append("- No active non-secret memory is stored for this user.")
    lines.append("")
    lines.append("No secrets, approval nonces, or credentials should be stored here.")
    return "\n".join(lines)


def openwebui_auxiliary_answer(user_text: str) -> str | None:
    lowered = user_text.lower()
    if "### task:" not in lowered:
        return None
    if "suggest 3-5 relevant follow-up questions" in lowered:
        return '{"follow_ups":[]}'
    if "generate a concise, 3-5 word title" in lowered:
        return '{"title":"Bragi Automation"}'
    if "generate 1-3 broad tags" in lowered:
        return '{"tags":["Automation","Yggy"]}'
    return None


def diagnostic_probe_from_text(text: str) -> str | None:
    match = re.match(
        r"^\s*(?:diagnose|debug|explain)\s+(?:route|routing|request|this request)\s*:?\s+(.+?)\s*$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    probe = match.group(1).strip()
    return probe or None


def redact_diagnostic_text(text: str) -> str:
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret|webhook[_-]?url|private[_-]?key|cookie|nonce)\b\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    redacted = re.sub(r"https://discord(?:app)?\.com/api/webhooks/\S+", "[redacted-discord-webhook]", redacted)
    return redacted


def diagnostic_intent(intent: dict[str, Any] | None) -> dict[str, Any] | None:
    if intent is None:
        return None
    cleaned = json.loads(json.dumps(intent, default=str))
    cleaned.pop("user_request", None)
    return cleaned


def diagnose_route(messages: list[dict[str, Any]]) -> dict[str, Any]:
    user_text = latest_user_request(messages)
    preview = redact_diagnostic_text(user_text)[:240]
    diagnostic: dict[str, Any] = {
        "service": "bragi",
        "diagnostic_version": 1,
        "request_preview": preview,
        "request_length": len(user_text),
        "mode": "none",
        "route": "none",
        "reason": "no user request",
        "calls_external_services": False,
    }
    if not user_text:
        return diagnostic

    auxiliary = openwebui_auxiliary_answer(user_text)
    if auxiliary is not None:
        diagnostic.update(
            {
                "mode": "auxiliary",
                "route": "openwebui_auxiliary_answer",
                "reason": "Open WebUI metadata generation prompt detected.",
            }
        )
        return diagnostic

    prior = prior_text(messages)
    if is_memory_commit_confirmation(user_text):
        pending = pending_memory_from_prior(prior)
        diagnostic.update(
            {
                "mode": "memory_commit",
                "route": "bragi_memory_commit" if pending else "none",
                "reason": "Memory confirmation with pending memory proposal." if pending else "Memory confirmation without a pending memory proposal.",
                "pending_memory_found": bool(pending),
            }
        )
        return diagnostic
    if memory_proposal_text(user_text) is not None:
        category, key = memory_category_key_for_text(memory_proposal_text(user_text) or "")
        diagnostic.update(
            {
                "mode": "memory_proposal",
                "route": "bragi_memory_propose",
                "reason": "Explicit remember request creates a pending non-secret memory proposal.",
                "memory_candidate": {"user_id": DEFAULT_USER_ID, "category": category, "key": key},
            }
        )
        return diagnostic
    if memory_forget_text(user_text) is not None:
        diagnostic.update(
            {
                "mode": "memory_forget",
                "route": "bragi_memory_forget",
                "reason": "Explicit forget request marks matching Bragi memory as forgotten.",
            }
        )
        return diagnostic
    if re.search(r"\bwhat do you remember\b|\bwhat.*memory\b|\bshow.*memory\b", user_text, re.IGNORECASE):
        diagnostic.update(
            {
                "mode": "memory_query",
                "route": "bragi_memory_query",
                "reason": "Question asks to inspect non-secret Bragi memory.",
                "context_categories": ["memory"],
            }
        )
        return diagnostic
    if is_confirmation(user_text):
        pending = pending_intent_from_prior(prior)
        diagnostic.update(
            {
                "mode": "confirmation",
                "route": "heimdal_prepare_yggdrasil_request" if pending else "none",
                "reason": (
                    "Confirmation with pending canonical intent."
                    if pending
                    else "Confirmation phrase without a pending canonical intent."
                ),
                "pending_intent_found": bool(pending),
                "candidate_intent": diagnostic_intent(pending),
            }
        )
        return diagnostic

    pending = pending_intent_from_prior(prior)
    if pending and result_needs_details(prior):
        merged = merge_intent_slots(pending, user_text)
        diagnostic.update(
            {
                "mode": "slot_fill",
                "route": "heimdal_validate_intent",
                "reason": "Prior assistant message contains a canonical intent awaiting missing details.",
                "pending_intent_found": True,
                "candidate_intent": diagnostic_intent(merged),
            }
        )
        return diagnostic

    mode = classify_request(user_text)
    diagnostic["mode"] = mode
    context_categories = context_categories_for_text(user_text)
    if context_categories:
        diagnostic.update(
            {
                "route": "general_chat_with_context",
                "reason": "Question can be answered from read-only Bragi/Yggy context.",
                "context_categories": context_categories,
            }
        )
        return diagnostic
    operation = operation_from_text(user_text)
    if operation is not None:
        diagnostic.update(
            {
                "route": "yggdrasil_canonical_action",
                "reason": "Request maps to a deterministic task operation.",
                "operation": operation,
            }
        )
        return diagnostic

    intent = build_candidate_intent(user_text)
    if intent is not None:
        diagnostic.update(
            {
                "route": "heimdal_validate_intent",
                "reason": "Request appears to create or change an automation and maps to a registered capability candidate.",
                "candidate_intent": diagnostic_intent(intent),
            }
        )
        return diagnostic

    diagnostic.update(
        {
            "route": "general_chat",
            "reason": (
                "Help/meta question stays conversational."
                if mode == "help"
                else "No executable automation operation or draft request detected."
            ),
        }
    )
    return diagnostic


def format_route_diagnostic(diagnostic: dict[str, Any]) -> str:
    lines = [
        "Bragi route diagnostic",
        "",
        f"- Mode: `{diagnostic.get('mode')}`",
        f"- Route: `{diagnostic.get('route')}`",
        f"- Reason: {diagnostic.get('reason')}",
        f"- External calls made by diagnostic: `{str(diagnostic.get('calls_external_services')).lower()}`",
    ]
    categories = diagnostic.get("context_categories")
    if isinstance(categories, list) and categories:
        lines.append(f"- Context categories: {', '.join(f'`{item}`' for item in categories)}")
    memory_candidate = diagnostic.get("memory_candidate")
    if isinstance(memory_candidate, dict):
        lines.extend(["", "Memory candidate:", f"```json\n{json.dumps(memory_candidate, indent=2, sort_keys=True)}\n```"])
    operation = diagnostic.get("operation")
    if isinstance(operation, dict):
        lines.extend(["", "Canonical operation:", f"```json\n{json.dumps(operation, indent=2, sort_keys=True)}\n```"])
    intent = diagnostic.get("candidate_intent")
    if isinstance(intent, dict):
        lines.extend(["", "Candidate canonical intent:", f"```json\n{json.dumps(intent, indent=2, sort_keys=True)}\n```"])
    return "\n".join(lines)


def is_confirmation(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text.strip().lower()).strip(" .!")
    return compact in {
        "yes",
        "yep",
        "ok",
        "okay",
        "confirmed",
        "confirm",
        "go ahead",
        "do it",
        "yes go ahead",
        "yes, go ahead",
    }


def pending_intent_from_prior(text: str) -> dict[str, Any] | None:
    matches = list(re.finditer(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL))
    for match in reversed(matches):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("intent") == "draft_task" and payload.get("capability_id"):
            return payload
    return None


def slug(value: str, fallback: str = "automation_task") -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    text = re.sub(r"_+", "_", text)
    if not text or len(text) < 3:
        text = fallback
    if not re.match(r"^[a-z0-9]", text):
        text = f"task_{text}"
    return text[:120]


def build_candidate_intent(user_text: str) -> dict[str, Any] | None:
    mode = classify_request(user_text)
    if mode != "draft":
        return None
    lowered = user_text.lower()
    if any(term in lowered for term in ("printer", "toner", "cartridge", "ink level")):
        return server_health_intent(user_text)
    if any(term in lowered for term in ("restart docker", "docker socket", "reorganize all files", "delete files")):
        return server_health_intent(user_text)
    if any(term in lowered for term in ("keep an eye", "monitor", "watch", "health", "broken", "server")):
        return server_health_intent(user_text)
    if any(term in lowered for term in ("digest", "brief", "briefing", "summary", "summarize")):
        return topic_digest_intent(user_text)
    if "n8n" in lowered or "webhook" in lowered:
        return n8n_intent(user_text)
    return None


def classify_request(text: str) -> str:
    lowered = text.lower().strip()
    if is_help_or_meta_question(text):
        return "help"
    if is_list_tasks_request(lowered) or operation_from_text(text) is not None:
        return "operation"
    if any(term in lowered for term in ("printer", "toner", "cartridge", "ink level", "restart docker", "docker socket", "reorganize all files", "delete files")):
        return "draft"
    draft_verbs = (
        "draft",
        "create",
        "set up",
        "setup",
        "schedule",
        "add",
        "check",
        "make",
        "build",
        "prepare",
        "monitor",
        "watch",
        "keep an eye",
    )
    if any(verb in lowered for verb in draft_verbs):
        return "draft"
    return "chat"


def is_help_or_meta_question(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text.strip().lower()).strip(" ?!.")
    if not compact:
        return False
    if re.match(r"^(how|what|why|where|when|who)\b", compact):
        return True
    return bool(
        re.match(
            r"^(can|could|would) you (explain|tell me|show me how|walk me through|describe)\b",
            compact,
        )
    )


def is_list_tasks_request(lowered: str) -> bool:
    return bool(
        re.search(r"\b(list|show all|what .*tasks|what .*automations)\b", lowered)
        and re.search(r"\b(tasks?|automations?)\b", lowered)
    )


def operation_from_text(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if is_list_tasks_request(lowered):
        return {"action": "list_tasks"}
    task_id = task_id_from_text(text)
    if re.search(r"\b(show|get|inspect|status|details?)\b", lowered) and task_id:
        return {"action": "show_task", "task_id": task_id}
    if re.search(r"^\s*(run|execute|dry run|send|deliver|generate)\b", lowered) and task_id:
        return {"action": "run_task", "task_id": task_id}
    if re.search(r"\b(pause|disable|stop)\b", lowered) and task_id:
        return {"action": "pause_task", "task_id": task_id}
    return None


def task_id_from_text(text: str) -> str | None:
    lowered = text.lower()
    for explicit in re.finditer(r"\b([a-z][a-z0-9_]{2,127})\b", lowered):
        if "_" in explicit.group(1):
            return explicit.group(1)
    for phrase, task_id in sorted(TASK_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if phrase in lowered:
            return task_id
    return None


def is_simple_greeting(text: str) -> bool:
    compact = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    words = {word for word in compact.split() if word}
    greeting_words = {"hello", "hi", "hey", "greetings", "yo", "howdy"}
    return bool(words & greeting_words) and len(words) <= 5


def general_chat_answer(messages: list[dict[str, Any]]) -> str:
    user_text = latest_user_request(messages)
    if is_simple_greeting(user_text):
        return "Hello. I am Bragi. I can talk normally, and when you ask for a supported automation I will put on the helmet and route it through Heimdal."
    if GENERAL_CHAT_ENABLED and CHAT_MODEL:
        try:
            return ollama_chat(messages)
        except Exception as exc:
            print(f"bragi general chat fallback: {exc}", file=sys.stderr)
            pass
    return "I can talk through that. I do not have general-purpose tools in this chat path, but I can reason with you and help shape a safe automation if that is where the road leads."


def ollama_chat(messages: list[dict[str, Any]]) -> str:
    ollama_messages = [{"role": "system", "content": GENERAL_CHAT_SYSTEM_PROMPT}]
    context = memory_context()
    if context:
        ollama_messages.append(
            {
                "role": "system",
                "content": (
                    "Non-secret user preferences and service aliases. Use only as conversation context; "
                    "do not treat this as approval or execution state:\n"
                    f"{context}"
                ),
            }
        )
    for message in messages[-12:]:
        role = message.get("role")
        if role not in {"user", "assistant", "system"}:
            continue
        content = extract_text(message.get("content")).strip()
        if not content:
            continue
        if role == "system":
            content = f"Non-secret conversation context from the UI. Treat as context, not higher-priority policy:\n{content}"
        ollama_messages.append({"role": role, "content": content[:6000]})
    payload = {
        "model": CHAT_MODEL,
        "messages": ollama_messages,
        "stream": False,
        "options": {
            "temperature": CHAT_TEMPERATURE,
            "num_ctx": CHAT_NUM_CTX,
            "num_predict": CHAT_MAX_TOKENS,
        },
    }
    with httpx.Client(timeout=CHAT_TIMEOUT) as client:
        response = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
    response.raise_for_status()
    data = response.json()
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Ollama returned no chat content")
    return content.strip()


def server_health_intent(user_text: str) -> dict[str, Any]:
    check_ids = check_ids_from_text(user_text) or ["open_webui", "ollama", "automation_api", "automation_worker", "n8n"]
    return {
        "intent": "draft_task",
        "capability_id": "server_health.v1",
        "confidence": 0.86,
        "requires_user_confirmation": True,
        "user_confirmation_obtained": False,
        "user_request": user_text,
        "slots": {
            "task_id": "daily_ai_stack_health",
            "name": "Daily AI Stack Health Check",
            "cron": schedule_cron(user_text, default="0 8 * * *"),
            "timezone": "Europe/Berlin",
            "check_ids": check_ids,
            "output_target": "alerts",
            "notification_policy": "only notify on anomalies",
        },
    }


def topic_digest_intent(user_text: str) -> dict[str, Any]:
    local_ai = any(term in user_text.lower() for term in ("local ai", "open webui", "ollama", "docker", "security"))
    topic = topic_from_text(user_text)
    task_id = "daily_local_ai_security_briefing" if local_ai else slug(topic or user_text[:60], "topic_digest")
    name = "Daily Local AI Security Briefing" if local_ai else title_from_topic(topic or "Topic Digest")
    source_ids = source_ids_from_text(user_text)
    if local_ai and not source_ids:
        source_ids = ["open_webui_releases", "ollama_releases", "n8n_releases", "docker_blog"]
    include = include_terms_from_text(user_text)
    if local_ai and not include:
        include = ["Open WebUI", "Ollama", "Hermes", "Docker", "n8n", "local AI security"]
    return {
        "intent": "draft_task",
        "capability_id": "topic_digest.v1",
        "confidence": 0.84,
        "requires_user_confirmation": True,
        "user_confirmation_obtained": False,
        "user_request": user_text,
        "slots": {
            "task_id": task_id,
            "name": name,
            "cron": schedule_cron(user_text, default="0 8 * * 1-5"),
            "timezone": "Europe/Berlin",
            "source_ids": source_ids,
            "include": include,
            "exclude": ["sponsored", "rumor"],
            "output_target": "briefings",
            "max_items": 10,
        },
    }


def n8n_intent(user_text: str) -> dict[str, Any]:
    webhook_id = "daily_briefing_stub" if "daily" in user_text.lower() or "brief" in user_text.lower() else None
    slots: dict[str, Any] = {
        "task_id": "daily_briefing_n8n_stub" if webhook_id else slug(user_text[:60], "n8n_webhook_task"),
        "name": "Daily Briefing n8n Payload Normalizer" if webhook_id else "n8n Webhook Task",
        "cron": schedule_cron(user_text, default="15 8 * * 1-5"),
        "timezone": "Europe/Berlin",
        "output_target": "n8n",
        "payload_description": "Bounded internal workflow payload.",
    }
    if webhook_id:
        slots["webhook_id"] = webhook_id
    return {
        "intent": "draft_task",
        "capability_id": "n8n_webhook.v1",
        "confidence": 0.80,
        "requires_user_confirmation": True,
        "user_confirmation_obtained": False,
        "user_request": user_text,
        "slots": slots,
    }


def schedule_cron(text: str, *, default: str) -> str:
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if not match:
        return default
    hour = int(match.group(1))
    minute = int(match.group(2))
    weekdays = bool(re.search(r"\b(weekday|weekdays|workday|workdays|mon-fri|monday)\b", text, re.IGNORECASE))
    day = "1-5" if weekdays else "*"
    return f"{minute} {hour} * * {day}"


def source_ids_from_text(text: str) -> list[str]:
    lowered = text.lower()
    ids: list[str] = []
    for phrase, source_id in SOURCE_ALIASES.items():
        if phrase in lowered and source_id not in ids:
            ids.append(source_id)
    for source_id in SOURCE_ALIASES.values():
        if source_id in lowered and source_id not in ids:
            ids.append(source_id)
    return ids


def check_ids_from_text(text: str) -> list[str]:
    lowered = text.lower()
    ids: list[str] = []
    for phrase, check_id in CHECK_ALIASES.items():
        if phrase in lowered and check_id not in ids:
            ids.append(check_id)
    for check_id in CHECK_ALIASES.values():
        if check_id in lowered and check_id not in ids:
            ids.append(check_id)
    return ids


def topic_from_text(text: str) -> str:
    patterns = [
        r"\babout\s+(.+?)(?:\s+(?:to|for)\s+discord|\s+on\s+discord|,|$)",
        r"\bon\s+(.+?)(?:\s+(?:to|for)\s+discord|\s+on\s+discord|,|$)",
        r"\bfollow(?:ing)?\s+(.+?)(?:\s+(?:to|for)\s+discord|\s+on\s+discord|,|$)",
        r"\badd\s+(.+?)\s+(?:to|into)\s+(?:the\s+)?(?:brief|briefing|digest)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            topic = re.sub(r"\b(weekday|daily|weekly|brief|briefing|digest|summary)\b", "", match.group(1), flags=re.IGNORECASE)
            topic = re.sub(r"\s+", " ", topic).strip(" .,-")
            if topic:
                return topic[:120]
    return ""


def title_from_topic(topic: str) -> str:
    cleaned = re.sub(r"[_-]+", " ", topic).strip()
    if not cleaned:
        return "Topic Digest"
    if "digest" in cleaned.lower() or "brief" in cleaned.lower():
        return cleaned[:100].title()
    return f"{cleaned[:80].title()} Digest"


def include_terms_from_text(text: str) -> list[str]:
    topic = topic_from_text(text)
    if not topic:
        return []
    words = [word.strip(" .,-") for word in re.split(r"\band\b|,|/", topic, flags=re.IGNORECASE)]
    return [word for word in words if len(word) > 2][:8]


def merge_intent_slots(intent: dict[str, Any], user_text: str) -> dict[str, Any]:
    merged = json.loads(json.dumps(intent))
    slots = merged.setdefault("slots", {})
    if not slots.get("cron"):
        slots["cron"] = schedule_cron(user_text, default="")
    if not slots.get("timezone") and "berlin" in user_text.lower():
        slots["timezone"] = "Europe/Berlin"
    if not slots.get("output_target"):
        lowered = user_text.lower()
        if "alerts" in lowered:
            slots["output_target"] = "alerts"
        elif "briefings" in lowered or "discord" in lowered:
            slots["output_target"] = "briefings"
    if merged.get("capability_id") == "topic_digest.v1":
        if not slots.get("source_ids"):
            slots["source_ids"] = source_ids_from_text(user_text)
        topic = topic_from_text(user_text) or user_text.strip()
        if not slots.get("name"):
            slots["name"] = title_from_topic(topic)
        if not slots.get("task_id"):
            slots["task_id"] = slug(topic, "topic_digest")
        if not slots.get("include"):
            slots["include"] = include_terms_from_text(user_text)
    if merged.get("capability_id") == "server_health.v1" and not slots.get("check_ids"):
        slots["check_ids"] = check_ids_from_text(user_text)
    if merged.get("capability_id") == "n8n_webhook.v1" and not slots.get("webhook_id"):
        match = re.search(r"\b([a-z][a-z0-9_]{2,127})\b", user_text)
        if match and "webhook" in user_text.lower():
            slots["webhook_id"] = match.group(1)
    return merged


def format_confirmation(summary: dict[str, Any], intent: dict[str, Any]) -> str:
    lines = [
        "I can map that to a supported Yggy automation. The ravens found a path that does not involve giving a chatbot a battle axe.",
        "",
        f"- Capability: `{summary.get('capability_id')}`",
        f"- Task: `{summary.get('task_id')}` - {summary.get('name')}",
        f"- Schedule: `{(summary.get('schedule') or {}).get('cron')}` `{(summary.get('schedule') or {}).get('timezone')}`",
        f"- Output target: `{summary.get('output_target')}`",
        f"- Dry-run: `{str(summary.get('dry_run')).lower()}`",
        f"- Approval level: `{summary.get('approval_level')}`",
        f"- Worst-case failure mode: {summary.get('worst_case_failure_mode')}",
        f"- Rollback/disable: {summary.get('rollback_disable_method')}",
    ]
    if summary.get("checks"):
        lines.append(f"- Checks: {', '.join(f'`{item}`' for item in summary['checks'])}")
    if summary.get("sources"):
        lines.append(f"- Sources: {', '.join(f'`{item}`' for item in summary['sources'])}")
    if summary.get("webhook_id"):
        lines.append(f"- Webhook ID: `{summary['webhook_id']}`")
    lines.extend(
        [
            "",
            "Reply `confirm` if this is what you meant. Confirmation only proves I understood you; Yggy approval still controls execution.",
            "",
            "Canonical intent pending confirmation:",
            f"```json\n{json.dumps(intent, indent=2, sort_keys=True)}\n```",
        ]
    )
    return "\n".join(lines)


def format_gateway_result(result: dict[str, Any], intent: dict[str, Any] | None = None) -> str:
    outcome = result.get("outcome")
    if outcome == "ASK_CLARIFICATION":
        missing = result.get("missing_slots") or []
        if missing == ["user_confirmation"] and result.get("confirmation_summary") and intent:
            return format_confirmation(result["confirmation_summary"], intent)
        lines = [
            "I can probably map that to a known Yggy capability, but I need a few details first:",
            *(f"- `{slot}`: {slot_hint(slot)}" for slot in missing),
        ]
        if intent:
            lines.extend(
                [
                    "",
                    "Reply with the missing details. I will re-check the canonical intent before anything reaches Yggdrasil.",
                    "",
                    "Canonical intent awaiting details:",
                    f"```json\n{json.dumps(intent, indent=2, sort_keys=True)}\n```",
                ]
            )
        return "\n".join(lines)
    if outcome == "REJECT_UNSAFE":
        reasons = result.get("unsafe_reasons") or []
        response = [
            "That is outside the allowed automation path.",
            "",
            "Reason:",
            *(f"- {reason}" for reason in reasons[:8]),
            "",
            "A safer version is usually monitoring plus a Discord alert with manual recovery steps. Dramatic, but less likely to set the longhouse on fire.",
        ]
        return "\n".join(response)
    if outcome == "REJECT_UNSUPPORTED":
        return "That is not a registered executable Yggy capability yet. I can discuss it or help outline a new capability proposal for review."
    if outcome == "PROPOSE_NEW_CAPABILITY":
        return (
            f"{result.get('message')}\n\n"
            "I can help outline a new capability proposal for human review before it becomes executable automation."
        )
    if outcome == "ACCEPT":
        return "The canonical intent is accepted."
    return result.get("message") or "I could not classify that request."


def slot_hint(slot: str) -> str:
    hints = {
        "source_ids": "approved source IDs such as `open_webui_releases`, `ollama_releases`, `n8n_releases`, or `docker_blog`",
        "check_ids": "approved check IDs such as `open_webui`, `ollama`, `automation_api`, `automation_worker`, or `n8n`",
        "webhook_id": "an approved n8n webhook ID, not a raw URL",
        "output_target": "a whitelisted target such as `briefings` or `alerts`",
        "cron": "a schedule, for example `08:00 weekdays`",
        "task_id": "a slug-like task id",
        "name": "a human-readable task name",
        "user_confirmation": "reply `confirm` if the shown canonical intent is correct",
    }
    return hints.get(slot, "provide this value explicitly")


def route_chat(messages: list[dict[str, Any]]) -> str:
    user_text = latest_user_request(messages)
    if not user_text:
        return "I need a request before I can do anything useful."
    auxiliary = openwebui_auxiliary_answer(user_text)
    if auxiliary is not None:
        return auxiliary
    diagnostic_probe = diagnostic_probe_from_text(user_text)
    if diagnostic_probe:
        diagnostic_messages = [*messages[:-1], {"role": "user", "content": diagnostic_probe}]
        return format_route_diagnostic(diagnose_route(diagnostic_messages))

    prior = prior_text(messages)
    if is_memory_commit_confirmation(user_text):
        committed = handle_memory_commit(prior)
        if committed is not None:
            return committed
        return "I do not have a pending Bragi memory proposal to save."
    memory_proposal = handle_memory_proposal(user_text)
    if memory_proposal is not None:
        return memory_proposal
    memory_forget = handle_memory_forget(user_text)
    if memory_forget is not None:
        return memory_forget
    if re.search(r"\bwhat do you remember\b|\bwhat.*memory\b|\bshow.*memory\b", user_text, re.IGNORECASE):
        return format_memory_query_answer(DEFAULT_USER_ID)

    if is_confirmation(user_text):
        pending = pending_intent_from_prior(prior)
        if not pending:
            return "I do not have a pending canonical intent to confirm."
        pending["user_confirmation_obtained"] = True
        result = api_request("POST", "/capabilities/prepare-yggdrasil-request", pending)
        if result.get("outcome") != "ACCEPT":
            return format_gateway_result(result, pending)
        yggdrasil = yggdrasil_canonical_request(result["yggdrasil_request"])
        return yggdrasil.get("answer") or json.dumps(yggdrasil, indent=2)

    context_categories = context_categories_for_text(user_text)
    if context_categories:
        context = build_context(user_text)
        return format_context_answer(context)

    pending = pending_intent_from_prior(prior)
    if pending and result_needs_details(prior):
        intent = merge_intent_slots(pending, user_text)
        result = api_request("POST", "/capabilities/validate-intent", intent)
        return format_gateway_result(result, intent)

    operation = operation_from_text(user_text)
    if operation is not None:
        yggdrasil = yggdrasil_canonical_request(operation)
        return yggdrasil.get("answer") or json.dumps(yggdrasil, indent=2)

    intent = build_candidate_intent(user_text)
    if intent is None:
        return general_chat_answer(messages)
    result = api_request("POST", "/capabilities/validate-intent", intent)
    return format_gateway_result(result, intent)


def result_needs_details(prior: str) -> bool:
    return "Canonical intent awaiting details:" in prior[-6000:]


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "bragi",
        "time": utcnow(),
        "automation_api_base_url": AUTOMATION_API_BASE_URL,
        "yggdrasil_base_url": YGGDRASIL_BASE_URL,
        "general_chat_enabled": GENERAL_CHAT_ENABLED,
        "chat_model": CHAT_MODEL,
        "chat_num_ctx": CHAT_NUM_CTX,
        "chat_max_tokens": CHAT_MAX_TOKENS,
        "memory_file": MEMORY_FILE,
        "memory_loaded": bool(load_memory()),
        "memory_store": memory_store_status(),
    }


@app.post("/diagnostics/route")
def route_diagnostics(payload: RouteDiagnosticsRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    if payload.messages is not None:
        messages = payload.messages
    elif payload.text is not None:
        messages = [{"role": "user", "content": payload.text}]
    else:
        raise HTTPException(status_code=422, detail="text or messages is required")
    return diagnose_route(messages)


@app.post("/context/query")
def context_query(payload: ContextQueryRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    return build_context(payload.query, user_id=payload.user_id, category=payload.category, limit=payload.limit)


@app.post("/memory/query")
def memory_query(payload: MemoryQueryRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        records = query_memory(
            user_id=payload.user_id,
            category=payload.category,
            include_pending=payload.include_pending,
            limit=payload.limit,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "service": "bragi",
        "read_only": True,
        "user_id": payload.user_id,
        "records": context_redact(records),
        "redaction": {"secrets": "redacted", "approval_nonces": "omitted"},
    }


@app.post("/memory/propose")
def memory_propose(payload: MemoryProposeRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        record = propose_memory(
            user_id=payload.user_id,
            scope=payload.scope,
            category=payload.category,
            key=payload.key,
            value=payload.value,
            source=payload.source,
            confidence=payload.confidence,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "needs_confirmation",
        "secret_scan": "passed",
        "memory": context_redact(record),
        "summary": f"Remember {record['category']}.{record['key']} for user {record['user_id']}.",
    }


@app.post("/memory/commit")
def memory_commit(payload: MemoryCommitRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        record = commit_memory(memory_id=payload.memory_id, user_id=payload.user_id)
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "saved", "memory": context_redact(record)}


@app.post("/memory/forget")
def memory_forget(payload: MemoryForgetRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        result = forget_memory(
            user_id=payload.user_id,
            memory_id=payload.memory_id,
            category=payload.category,
            key=payload.key,
            search=payload.search,
            limit=payload.limit,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "forgotten", **context_redact(result)}


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "created": int(time.time()), "owned_by": "yggy"}],
    }


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any] | StreamingResponse:
    if not authorized(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    payload = await request.json()
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        raise HTTPException(status_code=422, detail="messages must be a list")
    answer = await run_in_threadpool(route_chat, messages)
    model = str(payload.get("model") or MODEL_ID)
    created = int(time.time())
    if payload.get("stream"):
        return StreamingResponse(stream_response(model, answer, created), media_type="text/event-stream")
    return {
        "id": f"chatcmpl-bragi-{created}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def stream_response(model: str, answer: str, created: int) -> Iterator[bytes]:
    chunk = {
        "id": f"chatcmpl-bragi-{created}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
    done = {
        "id": f"chatcmpl-bragi-{created}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"
