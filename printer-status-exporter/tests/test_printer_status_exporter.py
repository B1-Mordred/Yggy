from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "printer-status-exporter"))

from printer_exporter.config import PrinterExporterConfig, load_config  # noqa: E402
from printer_exporter.main import app, read_printer_supplies  # noqa: E402


def test_printer_config_rejects_non_http_url(tmp_path):
    path = tmp_path / "printers.yaml"
    path.write_text(
        """
version: 1
printers:
  - id: bad
    name: Bad
    type: http_json
    url: file:///etc/passwd
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="http or https"):
        load_config(path)


def test_printer_config_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "printers.yaml"
    path.write_text(
        """
version: 1
printers:
  - id: office
    name: Office
    supplies:
      - name: Black toner
        level_percent: 75
  - id: office
    name: Other Office
    supplies:
      - name: Black toner
        level_percent: 75
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_config(path)


def test_static_printer_supplies_are_normalized():
    config = PrinterExporterConfig.model_validate(
        {
            "version": 1,
            "printers": [
                {
                    "id": "office",
                    "name": "Office Printer",
                    "type": "static_json",
                    "supplies": [
                        {"name": "Black toner", "level_percent": "75%"},
                        {"name": "Cyan toner", "level_percent": 0.5},
                    ],
                }
            ],
        }
    )

    result = read_printer_supplies(config.printers[0])

    assert result["ok"] is True
    assert result["supplies"][0]["level_percent"] == 75
    assert result["supplies"][1]["level_percent"] == 50


def test_http_printer_uses_configured_get_only(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self) -> dict:
            return {"supplies": {"black": 12}}

    def fake_get(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        return Response()

    monkeypatch.setattr("printer_exporter.main.httpx.get", fake_get)
    config = PrinterExporterConfig.model_validate(
        {
            "version": 1,
            "printers": [
                {
                    "id": "office",
                    "name": "Office Printer",
                    "type": "http_json",
                    "url": "http://printer-adapter.local/supplies",
                    "timeout_seconds": 2,
                }
            ],
        }
    )

    result = read_printer_supplies(config.printers[0])

    assert result["ok"] is True
    assert result["supplies"][0]["level_percent"] == 12
    assert calls == [{"url": "http://printer-adapter.local/supplies", "timeout": 2.0}]


def test_supplies_endpoint_uses_configured_printer(monkeypatch):
    config = PrinterExporterConfig.model_validate(
        {
            "version": 1,
            "printers": [
                {
                    "id": "office",
                    "name": "Office Printer",
                    "type": "static_json",
                    "supplies": [{"name": "Black toner", "level_percent": 75}],
                }
            ],
        }
    )

    monkeypatch.setattr("printer_exporter.main.load_config", lambda: config)

    response = TestClient(app).get("/printers/office/supplies")

    assert response.status_code == 200
    body = response.json()
    assert body["printer_id"] == "office"
    assert body["supplies"][0]["level_percent"] == 75
