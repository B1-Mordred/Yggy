from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException

from printer_exporter.config import PrinterSource, SupplyLevel, load_config

app = FastAPI(
    title="Yggy Printer Status Exporter",
    version="0.1.0",
    description="Narrow read-only local printer supply exporter.",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@app.get("/health")
def health() -> dict[str, Any]:
    config = load_config()
    return {
        "status": "ok",
        "service": "printer-status-exporter",
        "configured_printers": len(config.printers),
        "enabled_printers": sum(1 for printer in config.printers if printer.enabled),
        "time": utcnow(),
    }


@app.get("/printers")
def printers() -> dict[str, Any]:
    config = load_config()
    visible = [
        {
            "id": printer.id,
            "name": printer.name,
            "type": printer.type,
            "enabled": printer.enabled,
            "description": printer.description,
        }
        for printer in config.printers
    ]
    return {"status": "ok", "generated_at": utcnow(), "printers": visible}


@app.get("/printers/{printer_id}/supplies")
def printer_supplies(printer_id: str) -> dict[str, Any]:
    config = load_config()
    printer = next((item for item in config.printers if item.id == printer_id and item.enabled), None)
    if printer is None:
        raise HTTPException(status_code=404, detail="printer is not configured or enabled")
    result = read_printer_supplies(printer)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


def read_printer_supplies(printer: PrinterSource) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "printer_id": printer.id,
        "name": printer.name,
        "type": printer.type,
        "ok": False,
        "supplies": [],
    }
    if printer.type == "static_json":
        supplies = normalize_supplies([item.model_dump(mode="json") for item in printer.supplies])
        result.update(
            {
                "ok": bool(supplies),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "supplies": supplies,
            }
        )
        return result

    try:
        response = httpx.get(str(printer.url), timeout=printer.timeout_seconds)
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["status_code"] = response.status_code
        if response.status_code != printer.expected_status:
            return result
        supplies = normalize_supplies(response.json())
        result["supplies"] = supplies
        result["ok"] = bool(supplies)
    except Exception as exc:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["error"] = exc.__class__.__name__
    return result


def normalize_supplies(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_supplies = payload.get("supplies") or payload.get("consumables") or payload.get("levels") or []
    else:
        raw_supplies = payload

    if isinstance(raw_supplies, dict):
        raw_supplies = [{"name": name, "level_percent": value} for name, value in raw_supplies.items()]
    if not isinstance(raw_supplies, list):
        return []

    supplies: list[dict[str, Any]] = []
    for index, item in enumerate(raw_supplies):
        if isinstance(item, SupplyLevel):
            item = item.model_dump(mode="json")
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("color") or item.get("type") or f"supply_{index + 1}")[:120]
            level = first_present(item, "level_percent", "percent", "remaining_percent", "level")
            status = item.get("status")
        else:
            name = f"supply_{index + 1}"
            level = item
            status = None
        supplies.append(
            {
                "name": name,
                "level_percent": coerce_percent(level),
                **({"status": str(status)[:80]} if status is not None else {}),
            }
        )
    return supplies[:20]


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def coerce_percent(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(str(value).strip().rstrip("%"))
    except Exception:
        return None
    if 0.0 < number < 1.0:
        number *= 100
    if number < 0 or number > 100:
        return None
    return int(round(number))
