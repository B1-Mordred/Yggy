from __future__ import annotations

from worker.handlers.server_health import run_server_health


def test_server_health_handles_failed_endpoint_without_crashing(monkeypatch):
    def fail_get(*args, **kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr("worker.handlers.server_health.httpx.get", fail_get)
    result = run_server_health(
        {
            "checks": [{"type": "http_health", "name": "api", "url": "http://automation-api:8088/health"}],
            "runtime": {"timeout_seconds": 1},
        }
    )
    assert result["status"] == "degraded"
    assert result["checks"][0]["ok"] is False
    assert result["checks"][0]["error"] == "RuntimeError"
