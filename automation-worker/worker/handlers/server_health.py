from __future__ import annotations

import httpx


def run_server_health(task_config: dict) -> dict:
    timeout = task_config.get("runtime", {}).get("timeout_seconds", 60)
    results = []
    for check in task_config.get("checks", []):
        result = {"name": check.get("name"), "url": check.get("url"), "ok": False}
        try:
            response = httpx.get(check["url"], timeout=timeout)
            result["status_code"] = response.status_code
            result["ok"] = 200 <= response.status_code < 400
        except Exception as exc:
            result["error"] = exc.__class__.__name__
        results.append(result)
    return {"status": "ok" if all(item["ok"] for item in results) else "degraded", "checks": results}
