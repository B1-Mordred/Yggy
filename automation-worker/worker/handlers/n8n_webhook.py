from __future__ import annotations

import os
from collections.abc import Callable
from urllib.parse import urljoin

import httpx


def run_n8n_webhook(
    task_config: dict,
    *,
    run_id: str,
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
        "payload": n8n_config.get("payload") or {},
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
    return {
        "status": "ready",
        "notify": False,
        "webhook_id": webhook_id,
        "path": path,
        "status_code": response.status_code,
        "message": f"n8n webhook {webhook_id} dispatched.",
    }
