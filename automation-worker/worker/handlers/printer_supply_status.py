from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx


def run_printer_supply_status(task_config: dict, http_get: Callable[..., httpx.Response] = httpx.get) -> dict:
    timeout = task_config.get("runtime", {}).get("timeout_seconds", 60)
    results = [check_printer_supply(printer, timeout, http_get) for printer in task_config.get("printer_supplies", [])]
    anomalies = [item for item in results if not item.get("ok")]
    low_supplies = [supply for item in results for supply in item.get("low_supplies", []) if isinstance(supply, dict)]
    anomaly_only = "anomal" in str(task_config.get("output", {}).get("format", "")).lower()
    notify = bool(anomalies or not anomaly_only)
    return {
        "status": "ok" if not anomalies else "degraded",
        "printers": results,
        "ok_count": len(results) - len(anomalies),
        "failed_count": len(anomalies),
        "low_supply_count": len(low_supplies),
        "notify": notify,
        "message": render_printer_supply_message(task_config, results, anomalies, low_supplies, notify),
    }


def check_printer_supply(printer: dict, timeout: int, http_get: Callable[..., httpx.Response]) -> dict:
    started = time.monotonic()
    threshold = int(printer.get("low_threshold_percent") or 20)
    expected_status = int(printer.get("expected_status") or 200)
    result = {
        "printer_id": printer.get("printer_id"),
        "name": printer.get("name"),
        "type": printer.get("type", "http_json"),
        "url": printer.get("url"),
        "threshold_percent": threshold,
        "ok": False,
        "supplies": [],
        "low_supplies": [],
    }
    try:
        response = http_get(printer["url"], timeout=timeout)
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["status_code"] = response.status_code
        if response.status_code != expected_status:
            return result
        payload = response.json()
        supplies = normalize_supplies(payload)
        result["supplies"] = supplies
        result["supply_count"] = len(supplies)
        result["low_supplies"] = [
            supply for supply in supplies if isinstance(supply.get("level_percent"), int) and supply["level_percent"] <= threshold
        ]
        unknown = [supply for supply in supplies if supply.get("level_percent") is None]
        result["unknown_supply_count"] = len(unknown)
        result["ok"] = bool(supplies and not result["low_supplies"] and not unknown)
    except Exception as exc:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["error"] = exc.__class__.__name__
    return result


def normalize_supplies(payload: Any) -> list[dict[str, Any]]:
    raw_supplies: Any
    if isinstance(payload, dict):
        raw_supplies = payload.get("supplies") or payload.get("consumables") or payload.get("levels") or []
    else:
        raw_supplies = payload

    if isinstance(raw_supplies, dict):
        raw_supplies = [
            {"name": name, "level_percent": value}
            for name, value in raw_supplies.items()
        ]
    if not isinstance(raw_supplies, list):
        return []

    supplies: list[dict[str, Any]] = []
    for index, item in enumerate(raw_supplies):
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


def render_printer_supply_message(
    task_config: dict,
    printers: list[dict],
    anomalies: list[dict],
    low_supplies: list[dict],
    notify: bool,
) -> str:
    title = task_config.get("name", "Printer supply status")
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
            if item.get("low_supplies"):
                lows = ", ".join(f"{supply.get('name')} {supply.get('level_percent')}%" for supply in item["low_supplies"][:8])
                lines.append(f"- `{item.get('printer_id')}` low supply: {lows}")
            else:
                detail = item.get("error") or item.get("status_code") or "no readable supply levels"
                lines.append(f"- `{item.get('printer_id')}` {item.get('url')}: {detail}")
        lines.extend(["", "**Suggested action**", "Inspect the printer or read-only exporter before ordering supplies or changing configuration."])
    else:
        lines.append("No low printer supplies detected.")
        if not notify:
            lines.append("Discord alert suppressed by anomaly-only output policy.")

    lines.extend(["", "**Printers**"])
    for item in printers:
        status_label = "ok" if item.get("ok") else "failed"
        latency = item.get("latency_ms", "n/a")
        supply_count = item.get("supply_count", 0)
        lines.append(f"- `{item.get('printer_id')}`: {status_label}, supplies `{supply_count}`, latency `{latency}ms`")
    return "\n".join(lines)
