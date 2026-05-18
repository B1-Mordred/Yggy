from __future__ import annotations

from worker.handlers.printer_supply_status import run_printer_supply_status


class Response:
    def __init__(self, status_code: int = 200, payload: dict | list | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def json(self) -> dict | list:
        return self.payload


def printer_task(**overrides):
    task = {
        "name": "Daily Printer Supply Status",
        "printer_supplies": [
            {
                "printer_id": "printer_status_exporter_example",
                "name": "Printer Status Exporter Example",
                "type": "http_json",
                "url": "http://printer-status-exporter:8091/printers/printer_status_exporter_example/supplies",
                "low_threshold_percent": 20,
                "expected_status": 200,
            }
        ],
        "output": {"format": "anomalies only"},
        "runtime": {"timeout_seconds": 1, "dry_run": True},
    }
    task.update(overrides)
    return task


def test_printer_supply_status_suppresses_clean_anomaly_only_result():
    def ok_get(*args, **kwargs):
        return Response(200, {"supplies": [{"name": "Black toner", "level_percent": 75}]})

    result = run_printer_supply_status(printer_task(), http_get=ok_get)

    assert result["status"] == "ok"
    assert result["notify"] is False
    assert result["ok_count"] == 1
    assert result["failed_count"] == 0
    assert "Discord alert suppressed" in result["message"]


def test_printer_supply_status_detects_low_supply():
    def low_get(*args, **kwargs):
        return Response(200, {"supplies": [{"name": "Black toner", "percent": 12}]})

    result = run_printer_supply_status(printer_task(), http_get=low_get)

    assert result["status"] == "degraded"
    assert result["notify"] is True
    assert result["low_supply_count"] == 1
    assert result["printers"][0]["low_supplies"][0]["name"] == "Black toner"
    assert "**Anomalies**" in result["message"]


def test_printer_supply_status_handles_failed_endpoint_without_crashing():
    def fail_get(*args, **kwargs):
        raise RuntimeError("network unavailable")

    result = run_printer_supply_status(printer_task(), http_get=fail_get)

    assert result["status"] == "degraded"
    assert result["notify"] is True
    assert result["printers"][0]["ok"] is False
    assert result["printers"][0]["error"] == "RuntimeError"


def test_printer_supply_status_supports_supply_dict_payload():
    def dict_get(*args, **kwargs):
        return Response(200, {"supplies": {"black": "19%", "cyan": 64}})

    result = run_printer_supply_status(printer_task(), http_get=dict_get)

    assert result["status"] == "degraded"
    assert result["low_supply_count"] == 1
    assert result["printers"][0]["supplies"][0]["level_percent"] == 19


def test_printer_supply_status_percent_coercion_handles_fractions_and_single_digits():
    def dict_get(*args, **kwargs):
        return Response(200, {"supplies": {"black": 0.5, "cyan": 1}})

    result = run_printer_supply_status(printer_task(), http_get=dict_get)

    supplies = result["printers"][0]["supplies"]
    assert supplies[0]["level_percent"] == 50
    assert supplies[1]["level_percent"] == 1
    assert result["low_supply_count"] == 1
