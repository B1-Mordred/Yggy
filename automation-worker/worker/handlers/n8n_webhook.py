from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import httpx

MAX_RESPONSE_DEPTH = 5
MAX_RESPONSE_KEYS = 25
MAX_RESPONSE_ITEMS = 25
MAX_RESPONSE_STRING_LENGTH = 1000
SECRET_KEY_PARTS = ("authorization", "cookie", "password", "secret", "token", "credential")


def run_n8n_webhook(
    task_config: dict,
    *,
    run_id: str,
    payload_override: dict[str, Any] | None = None,
    http_post: Callable[..., httpx.Response] = httpx.post,
) -> dict:
    n8n_config = task_config.get("n8n") or {}
    webhook_id = n8n_config.get("webhook_id")
    path = n8n_config.get("path")
    dry_run = bool(task_config.get("runtime", {}).get("dry_run", True))
    if not webhook_id or not path:
        raise ValueError("n8n webhook task requires webhook_id and path")

    dispatch_payload = {
        "task_id": task_config["id"],
        "task_name": task_config.get("name"),
        "run_id": run_id,
        "webhook_id": webhook_id,
        "dry_run": dry_run,
        "payload": payload_override if payload_override is not None else n8n_config.get("payload") or {},
    }

    if dry_run:
        return {
            "status": "dry_run",
            "notify": False,
            "webhook_id": webhook_id,
            "path": path,
            "message": f"n8n webhook {webhook_id} dry-run; no network request sent.",
            "payload_keys": sorted(dispatch_payload["payload"].keys()),
        }

    token = os.getenv("N8N_WEBHOOK_SHARED_SECRET", "")
    if not token:
        raise ValueError("N8N_WEBHOOK_SHARED_SECRET is required for live n8n webhook dispatch")

    base_url = os.getenv("N8N_WEBHOOK_BASE_URL", "http://n8n:5678").rstrip("/") + "/"
    url = urljoin(base_url, path.lstrip("/"))
    response = http_post(
        url,
        json=dispatch_payload,
        headers={
            "X-Yggy-Webhook-Token": token,
            "X-Yggy-Task-Id": task_config["id"],
            "X-Yggy-Run-Id": run_id,
        },
        timeout=int(task_config.get("runtime", {}).get("timeout_seconds", 120)),
    )
    response.raise_for_status()
    result = {
        "status": "ready",
        "notify": False,
        "webhook_id": webhook_id,
        "path": path,
        "status_code": response.status_code,
        "message": f"n8n webhook {webhook_id} dispatched.",
    }
    response_body = safe_response_body(response)
    if response_body is not None:
        result["response"] = response_body
    return result


def safe_response_body(response: httpx.Response) -> Any:
    try:
        body = response.json()
    except (AttributeError, ValueError):
        text = getattr(response, "text", "")
        if not text:
            return None
        body = {"text": text}
    return sanitize_response_value(body)


def sanitize_response_value(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_RESPONSE_DEPTH:
        return "<truncated>"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_RESPONSE_KEYS:
                sanitized["_truncated_keys"] = len(value) - MAX_RESPONSE_KEYS
                break
            key_text = str(key)
            if is_secret_key(key_text):
                sanitized[key_text] = "<redacted>"
            else:
                sanitized[key_text] = sanitize_response_value(item, depth + 1)
        return sanitized
    if isinstance(value, list):
        sanitized_items = [sanitize_response_value(item, depth + 1) for item in value[:MAX_RESPONSE_ITEMS]]
        if len(value) > MAX_RESPONSE_ITEMS:
            sanitized_items.append({"_truncated_items": len(value) - MAX_RESPONSE_ITEMS})
        return sanitized_items
    if isinstance(value, str):
        if len(value) > MAX_RESPONSE_STRING_LENGTH:
            return f"{value[:MAX_RESPONSE_STRING_LENGTH]}...<truncated>"
        return value
    return value


def is_secret_key(key: str) -> bool:
    lower_key = key.lower()
    return any(part in lower_key for part in SECRET_KEY_PARTS)
