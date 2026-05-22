from __future__ import annotations

from datetime import datetime, timedelta, timezone

from worker.handlers.tls_certificate_expiry import check_tls_endpoint, run_tls_certificate_expiry
from worker.main import process_task

CERT_TIME_FORMAT = "%b %d %H:%M:%S %Y %Z"


class FakeClient:
    def __init__(self) -> None:
        self.discord_calls: list[dict] = []
        self.completed_calls: list[dict] = []
        self.claim_calls: list[str] = []

    def queue_run(self, task_id: str) -> dict:
        return {"run_id": f"run-{task_id}", "status": "queued_dry_run"}

    def claim_run(self, run_id: str) -> dict:
        self.claim_calls.append(run_id)
        return {"id": run_id, "status": "running_dry_run", "dry_run": True}

    def list_runs(self, task_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
        return []

    def send_discord(self, target: str, content: str, dry_run: bool) -> dict:
        self.discord_calls.append({"target": target, "content": content, "dry_run": dry_run})
        return {"sent": False, "target": target, "dry_run": dry_run}

    def complete_run(self, run_id: str, status: str, log: dict) -> dict:
        self.completed_calls.append({"run_id": run_id, "status": status, "log": log})
        return {"id": run_id, "status": status}


def cert_expires_at(value: datetime) -> dict:
    return {
        "subject": ((("commonName", "yggy.b1.germering"),),),
        "issuer": ((("commonName", "Test CA"),),),
        "notAfter": value.astimezone(timezone.utc).strftime(CERT_TIME_FORMAT),
    }


def tls_endpoint(**overrides):
    endpoint = {
        "endpoint_id": "yggy_ops_https",
        "host": "yggy.b1.germering",
        "port": 8443,
        "warning_threshold_days": 30,
        "critical_threshold_days": 14,
    }
    endpoint.update(overrides)
    return endpoint


def tls_task(**overrides):
    task = {
        "id": "yggy_ops_tls_certificate_expiry",
        "name": "Yggy Ops TLS Certificate Expiry",
        "type": "tls_certificate_expiry",
        "tls_endpoints": [tls_endpoint()],
        "output": {"channel": "discord", "target": "alerts", "format": "anomalies only"},
        "runtime": {"dry_run": True, "timeout_seconds": 1},
        "notifications": {"on_success": False, "on_failure": True},
    }
    task.update(overrides)
    return task


def test_tls_certificate_expiry_suppresses_clean_anomaly_only_result():
    now = datetime(2026, 5, 22, tzinfo=timezone.utc)

    def fetcher(endpoint: dict, timeout: int) -> dict:
        return cert_expires_at(now + timedelta(days=90))

    result = run_tls_certificate_expiry(tls_task(), certificate_fetcher=fetcher, now=now)

    assert result["status"] == "ok"
    assert result["notify"] is False
    assert result["ok_count"] == 1
    assert result["failed_count"] == 0
    assert "Discord alert suppressed" in result["message"]


def test_tls_certificate_expiry_detects_warning_threshold():
    now = datetime(2026, 5, 22, tzinfo=timezone.utc)

    def fetcher(endpoint: dict, timeout: int) -> dict:
        return cert_expires_at(now + timedelta(days=20))

    result = run_tls_certificate_expiry(tls_task(), certificate_fetcher=fetcher, now=now)

    assert result["status"] == "degraded"
    assert result["notify"] is True
    assert result["failed_count"] == 1
    assert result["endpoints"][0]["status"] == "warning"


def test_tls_certificate_expiry_detects_expired_certificate():
    now = datetime(2026, 5, 22, tzinfo=timezone.utc)

    def fetcher(endpoint: dict, timeout: int) -> dict:
        return cert_expires_at(now - timedelta(days=2))

    result = check_tls_endpoint(tls_endpoint(), 1, certificate_fetcher=fetcher, now=now)

    assert result["ok"] is False
    assert result["status"] == "expired"
    assert result["severity"] == "critical"


def test_tls_certificate_expiry_handles_handshake_failure_without_crashing():
    def fetcher(endpoint: dict, timeout: int) -> dict:
        raise RuntimeError("handshake unavailable")

    result = run_tls_certificate_expiry(tls_task(), certificate_fetcher=fetcher)

    assert result["status"] == "degraded"
    assert result["notify"] is True
    assert result["endpoints"][0]["status"] == "handshake_failed"
    assert result["endpoints"][0]["error"] == "RuntimeError"


def test_worker_dispatches_tls_certificate_expiry_and_suppresses_clean_notification(monkeypatch):
    client = FakeClient()

    def fake_tls(config: dict) -> dict:
        return {"status": "ok", "message": "tls ok", "notify": False, "failed_count": 0}

    monkeypatch.setattr("worker.main.run_tls_certificate_expiry", fake_tls)

    result = process_task(client, {"enabled": True, "config": tls_task()})

    assert result["status"] == "completed_dry_run"
    assert client.discord_calls == []
    assert client.completed_calls[0]["log"]["result"]["status"] == "ok"
    assert client.completed_calls[0]["log"]["notification_decision"]["reason"] == "handler_suppressed"
