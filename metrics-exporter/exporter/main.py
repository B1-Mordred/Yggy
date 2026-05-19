from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI

from exporter.config import ServiceCheck, load_config

app = FastAPI(
    title="Yggy Metrics Exporter",
    version="0.1.0",
    description="Narrow read-only local service metrics exporter.",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@app.get("/health")
def health() -> dict[str, Any]:
    config = load_config()
    return {
        "status": "ok",
        "service": "metrics-exporter",
        "configured_services": len(config.services),
        "enabled_services": sum(1 for service in config.services if service.enabled),
        "time": utcnow(),
    }


@app.get("/metrics/services")
def service_metrics() -> dict[str, Any]:
    config = load_config()
    checks = [check_service(service) for service in config.services if service.enabled]
    failed = [check for check in checks if not check.get("ok")]
    return {
        "status": "ok" if not failed else "degraded",
        "generated_at": utcnow(),
        "summary": {
            "configured_count": len(config.services),
            "enabled_count": len(checks),
            "ok_count": len(checks) - len(failed),
            "failed_count": len(failed),
        },
        "services": checks,
    }


def check_service(service: ServiceCheck) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "id": service.id,
        "name": service.name,
        "type": service.type,
        "url": service.url,
        "description": service.description,
        "ok": False,
    }
    try:
        response = httpx.get(service.url, timeout=service.timeout_seconds)
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["status_code"] = response.status_code
        result["ok"] = (
            response.status_code == service.expected_status
            if service.expected_status
            else 200 <= response.status_code < 400
        )
        enrich_result(result, service, response)
    except Exception as exc:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["error"] = exc.__class__.__name__
    return result


def enrich_result(result: dict[str, Any], service: ServiceCheck, response: httpx.Response) -> None:
    if service.type == "worker_heartbeat":
        payload = safe_json(response)
        worker = payload.get("worker") if isinstance(payload, dict) else {}
        result["worker_status"] = worker.get("status")
        result["worker_age_seconds"] = worker.get("age_seconds")
        result["worker_ok"] = worker.get("ok")
        result["ok"] = bool(result["ok"] and worker.get("ok") is True)
    elif service.type == "ollama_tags":
        payload = safe_json(response)
        models = payload.get("models") if isinstance(payload, dict) else []
        result["model_count"] = len(models) if isinstance(models, list) else 0
        result["ok"] = bool(result["ok"] and result["model_count"] > 0)


def safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
