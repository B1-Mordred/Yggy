from __future__ import annotations

from worker.handlers.server_health import run_server_health


class Response:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def json(self) -> dict:
        return self.payload


def test_server_health_handles_failed_endpoint_without_crashing():
    def fail_get(*args, **kwargs):
        raise RuntimeError("network unavailable")

    result = run_server_health(
        {
            "checks": [{"type": "http_health", "name": "api", "url": "http://automation-api:8088/health"}],
            "runtime": {"timeout_seconds": 1},
        },
        http_get=fail_get,
    )
    assert result["status"] == "degraded"
    assert result["notify"] is True
    assert result["checks"][0]["ok"] is False
    assert result["checks"][0]["error"] == "RuntimeError"
    assert "**Anomalies**" in result["message"]


def test_server_health_suppresses_anomaly_only_notification_when_ok():
    def ok_get(*args, **kwargs):
        return Response(200, {"status": "ok"})

    result = run_server_health(
        {
            "name": "Health",
            "checks": [{"type": "http_health", "name": "api", "url": "http://automation-api:8088/health"}],
            "output": {"format": "anomalies only"},
            "runtime": {"timeout_seconds": 1, "dry_run": False},
        },
        http_get=ok_get,
    )

    assert result["status"] == "ok"
    assert result["notify"] is False
    assert result["ok_count"] == 1
    assert result["failed_count"] == 0
    assert "Discord alert suppressed" in result["message"]


def test_server_health_worker_heartbeat_detects_stale_worker():
    def stale_get(*args, **kwargs):
        return Response(
            200,
            {"worker": {"ok": False, "status": "ok", "age_seconds": 999, "max_age_seconds": 180}},
        )

    result = run_server_health(
        {
            "checks": [
                {
                    "type": "worker_heartbeat",
                    "name": "automation_worker",
                    "url": "http://automation-api:8088/health",
                    "max_age_seconds": 180,
                }
            ],
            "output": {"format": "anomalies only"},
            "runtime": {"timeout_seconds": 1},
        },
        http_get=stale_get,
    )

    assert result["status"] == "degraded"
    assert result["checks"][0]["ok"] is False
    assert result["checks"][0]["worker_age_seconds"] == 999


def test_server_health_ollama_tags_requires_models():
    def ollama_get(*args, **kwargs):
        return Response(200, {"models": []})

    result = run_server_health(
        {
            "checks": [{"type": "ollama_tags", "name": "ollama", "url": "http://host.docker.internal:11434/api/tags"}],
            "output": {"format": "anomalies only"},
            "runtime": {"timeout_seconds": 1},
        },
        http_get=ollama_get,
    )

    assert result["status"] == "degraded"
    assert result["checks"][0]["model_count"] == 0


def test_server_health_service_metrics_detects_failed_services():
    def metrics_get(*args, **kwargs):
        return Response(
            200,
            {
                "status": "degraded",
                "summary": {"enabled_count": 3, "ok_count": 2, "failed_count": 1},
                "services": [
                    {"id": "automation_api", "ok": True},
                    {"id": "open_webui", "ok": False},
                ],
            },
        )

    result = run_server_health(
        {
            "checks": [{"type": "service_metrics", "name": "metrics", "url": "http://metrics-exporter:8090/metrics/services"}],
            "output": {"format": "anomalies only"},
            "runtime": {"timeout_seconds": 1},
        },
        http_get=metrics_get,
    )

    assert result["status"] == "degraded"
    assert result["checks"][0]["metrics_failed_count"] == 1
    assert result["checks"][0]["metrics_failed_services"] == ["open_webui"]
