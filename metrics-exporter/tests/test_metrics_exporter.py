from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "metrics-exporter"))

from exporter.config import MetricsConfig, load_config  # noqa: E402
from exporter.main import app, check_service  # noqa: E402


def test_metrics_config_rejects_non_http_url(tmp_path):
    path = tmp_path / "services.yaml"
    path.write_text(
        """
version: 1
services:
  - id: bad
    name: Bad
    url: file:///etc/passwd
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="http or https"):
        load_config(path)


def test_metrics_config_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "services.yaml"
    path.write_text(
        """
version: 1
services:
  - id: api
    name: API
    url: http://automation-api:8088/health
  - id: api
    name: Other API
    url: http://automation-api:8088/health
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_config(path)


def test_check_service_does_not_use_shell_or_docker(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self) -> dict:
            return {"status": "ok"}

    def fake_get(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        return Response()

    monkeypatch.setattr("exporter.main.httpx.get", fake_get)
    config = MetricsConfig.model_validate(
        {
            "version": 1,
            "services": [{"id": "api", "name": "API", "url": "http://automation-api:8088/health"}],
        }
    )

    result = check_service(config.services[0])

    assert result["ok"] is True
    assert calls == [{"url": "http://automation-api:8088/health", "timeout": 3.0}]


def test_service_metrics_summary(monkeypatch):
    config = MetricsConfig.model_validate(
        {
            "version": 1,
            "services": [
                {"id": "api", "name": "API", "url": "http://automation-api:8088/health"},
                {"id": "disabled", "name": "Disabled", "url": "http://example.invalid", "enabled": False},
            ],
        }
    )

    monkeypatch.setattr("exporter.main.load_config", lambda: config)
    monkeypatch.setattr("exporter.main.check_service", lambda service: {"id": service.id, "ok": True})

    response = TestClient(app).get("/metrics/services")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["summary"]["configured_count"] == 2
    assert body["summary"]["enabled_count"] == 1
    assert body["summary"]["ok_count"] == 1
    assert body["summary"]["failed_count"] == 0
