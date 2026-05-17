from __future__ import annotations

import time
from collections.abc import Callable

import httpx


def check_http(check: dict, timeout: int, http_get: Callable[..., httpx.Response]) -> dict:
    expected_status = check.get("expected_status")
    started = time.monotonic()
    result = {
        "type": check.get("type", "http_health"),
        "name": check.get("name"),
        "url": check.get("url"),
        "ok": False,
    }
    try:
        response = http_get(check["url"], timeout=timeout)
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["status_code"] = response.status_code
        result["ok"] = response.status_code == expected_status if expected_status else 200 <= response.status_code < 400
        if check.get("type") == "worker_heartbeat":
            payload = response.json()
            worker = payload.get("worker") if isinstance(payload, dict) else {}
            max_age = int(check.get("max_age_seconds") or worker.get("max_age_seconds") or 180)
            age = worker.get("age_seconds")
            result["worker_status"] = worker.get("status")
            result["worker_age_seconds"] = age
            result["max_age_seconds"] = max_age
            result["ok"] = bool(result["ok"] and worker.get("ok") is True and age is not None and int(age) <= max_age)
        if check.get("type") == "ollama_tags":
            payload = response.json()
            models = payload.get("models") if isinstance(payload, dict) else []
            result["model_count"] = len(models) if isinstance(models, list) else 0
            result["ok"] = bool(result["ok"] and result["model_count"] > 0)
        if check.get("type") == "service_metrics":
            payload = response.json()
            summary = payload.get("summary") if isinstance(payload, dict) else {}
            services = payload.get("services") if isinstance(payload, dict) else []
            failed_count = int(summary.get("failed_count") or 0) if isinstance(summary, dict) else 0
            enabled_count = int(summary.get("enabled_count") or 0) if isinstance(summary, dict) else 0
            result["metrics_status"] = payload.get("status") if isinstance(payload, dict) else None
            result["metrics_enabled_count"] = enabled_count
            result["metrics_failed_count"] = failed_count
            result["metrics_failed_services"] = [
                service.get("id") or service.get("name")
                for service in services
                if isinstance(service, dict) and service.get("ok") is not True
            ][:10]
            result["ok"] = bool(result["ok"] and enabled_count > 0 and failed_count == 0)
    except Exception as exc:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["error"] = exc.__class__.__name__
    return result


def render_health_message(task_config: dict, checks: list[dict], anomalies: list[dict], notify: bool) -> str:
    title = task_config.get("name", "Server health check")
    dry_run = task_config.get("runtime", {}).get("dry_run", True)
    status = "degraded" if anomalies else "ok"
    lines = [
        f"**{title}**",
        "",
        f"Status: {status}",
        f"Mode: {'dry-run' if dry_run else 'ready'}",
        "",
    ]
    if anomalies:
        lines.append("**Anomalies**")
        for item in anomalies:
            detail = item.get("error") or item.get("status_code") or item.get("worker_status") or "failed"
            lines.append(f"- `{item.get('name')}` {item.get('url')}: {detail}")
        lines.extend(["", "**Suggested action**", "Inspect the failed service endpoint before making changes."])
    else:
        lines.append("No anomalies detected.")
        if not notify:
            lines.append("Discord alert suppressed by anomaly-only output policy.")

    lines.extend(["", "**Checks**"])
    for item in checks:
        status_icon = "ok" if item.get("ok") else "failed"
        latency = item.get("latency_ms", "n/a")
        lines.append(f"- `{item.get('name')}`: {status_icon}, latency `{latency}ms`")
    return "\n".join(lines)


def run_server_health(task_config: dict, http_get: Callable[..., httpx.Response] = httpx.get) -> dict:
    timeout = task_config.get("runtime", {}).get("timeout_seconds", 60)
    results = []
    for check in task_config.get("checks", []):
        results.append(check_http(check, timeout, http_get))
    anomalies = [item for item in results if not item.get("ok")]
    anomaly_only = "anomal" in str(task_config.get("output", {}).get("format", "")).lower()
    notify = bool(anomalies or not anomaly_only)
    return {
        "status": "ok" if not anomalies else "degraded",
        "checks": results,
        "ok_count": len(results) - len(anomalies),
        "failed_count": len(anomalies),
        "notify": notify,
        "message": render_health_message(task_config, results, anomalies, notify),
    }
