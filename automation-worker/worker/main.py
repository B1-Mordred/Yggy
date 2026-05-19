from __future__ import annotations

import os
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from zoneinfo import ZoneInfo

from worker.clients.automation_api import AutomationApiClient
from worker.handlers.backup_verification import run_backup_verification
from worker.handlers.n8n_webhook import run_n8n_webhook
from worker.handlers.printer_supply_status import run_printer_supply_status
from worker.handlers.server_health import run_server_health
from worker.handlers.topic_digest import run_topic_digest
from worker.scheduler import due_tasks

_LAST_RETENTION_RUN_AT: float | None = None
_LAST_STALE_RUN_RECOVERY_AT: float | None = None
MAX_TOPIC_N8N_ITEMS = 10
MAX_TOPIC_N8N_SOURCES = 10
MAX_TOPIC_N8N_ERRORS = 10
MAX_TOPIC_N8N_TEXT_LENGTH = 2000


def result_message(config: dict, result: dict) -> str:
    if result.get("message"):
        return str(result["message"])
    return f"{config.get('name', config.get('id', 'Automation task'))}: {result}"


def truncate_text(value: object, limit: int = MAX_TOPIC_N8N_TEXT_LENGTH) -> str:
    text = str(value or "")
    if len(text) > limit:
        return f"{text[:limit]}...<truncated>"
    return text


def topic_digest_n8n_payload(config: dict, result: dict) -> dict:
    configured_payload = dict((config.get("n8n") or {}).get("payload") or {})
    items = result.get("items") if isinstance(result.get("items"), list) else []
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    normalized_items = []
    sources = []
    for item in items[:MAX_TOPIC_N8N_ITEMS]:
        if not isinstance(item, dict):
            continue
        source = item.get("link") or item.get("source") or ""
        if source and len(sources) < MAX_TOPIC_N8N_SOURCES:
            sources.append(source)
        normalized_items.append(
            {
                "title": truncate_text(item.get("title"), 300),
                "summary": truncate_text(item.get("summary"), 800),
                "url": truncate_text(source, 1000),
                "published": truncate_text(item.get("published"), 200),
                "type": truncate_text(item.get("type"), 100),
                "source_id": truncate_text(item.get("source_id"), 128),
                "source_name": truncate_text(item.get("source_name"), 300),
                "source_trust_level": truncate_text(item.get("source_trust_level"), 128),
            }
        )

    payload = {
        **configured_payload,
        "title": truncate_text(result.get("title") or config.get("name") or config.get("id"), 300),
        "summary": truncate_text(result.get("message") or "", 2000),
        "items": normalized_items,
        "sources": sources,
        "errors": [
            {
                "source": truncate_text(error.get("source"), 1000),
                "error": truncate_text(error.get("error"), 200),
            }
            for error in errors[:MAX_TOPIC_N8N_ERRORS]
            if isinstance(error, dict)
        ],
        "source_health": result.get("source_health") if isinstance(result.get("source_health"), list) else [],
        "summary_mode": truncate_text(result.get("summary_mode"), 100),
    }
    return payload


def maybe_run_topic_digest_n8n(config: dict, result: dict, *, run_id: str) -> dict:
    if not config.get("n8n"):
        return result
    n8n_result = run_n8n_webhook(config, run_id=run_id, payload_override=topic_digest_n8n_payload(config, result))
    return {**result, "n8n": n8n_result}


def failure_message(config: dict, error: Exception) -> str:
    return "\n".join(
        [
            f"**Task failed: {config.get('name', config.get('id', 'Automation task'))}**",
            "",
            f"Task: `{config.get('id', 'unknown')}`",
            f"Error: `{error.__class__.__name__}`",
            "",
            "Suggested action: inspect the run log and pause the task if the failure repeats.",
        ]
    )


def notification_preferences(config: dict) -> dict:
    prefs = dict(config.get("notifications") or {})
    quiet_hours = dict(prefs.get("quiet_hours") or {})
    return {
        "on_success": prefs.get("on_success", True),
        "on_failure": prefs.get("on_failure", True),
        "on_empty_result": prefs.get("on_empty_result", False),
        "quiet_hours": {
            "enabled": quiet_hours.get("enabled", False),
            "start": quiet_hours.get("start", "22:00"),
            "end": quiet_hours.get("end", "07:00"),
            "timezone": quiet_hours.get("timezone", config.get("trigger", {}).get("timezone", "Europe/Berlin")),
        },
        "collapse_repeated_failures": prefs.get("collapse_repeated_failures", True),
        "failure_collapse_window_minutes": int(prefs.get("failure_collapse_window_minutes", 360)),
    }


def classify_result(result: dict, failed: bool = False) -> str:
    if failed:
        return "failure"
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    if quality.get("status") in {"degraded", "failed"}:
        return "failure"
    status = str(result.get("status", "")).lower()
    if status in {"failed", "failure", "error", "degraded"} or int(result.get("failed_count") or 0) > 0:
        return "failure"
    if result.get("error") or (result.get("errors") and not result.get("items")):
        return "failure"
    if "items" in result and not result.get("items"):
        return "empty"
    return "success"


def quiet_hours_active(preferences: dict, now: datetime | None = None) -> bool:
    quiet = preferences.get("quiet_hours", {})
    if not quiet.get("enabled", False):
        return False
    zone = ZoneInfo(str(quiet.get("timezone") or "Europe/Berlin"))
    current = now.astimezone(zone) if now else datetime.now(zone)
    start = _parse_hhmm(str(quiet.get("start", "22:00")))
    end = _parse_hhmm(str(quiet.get("end", "07:00")))
    current_time = current.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= current_time < end
    return current_time >= start or current_time < end


def _parse_hhmm(value: str) -> clock_time:
    hour, minute = value.split(":", 1)
    return clock_time(int(hour), int(minute))


def notification_decision(
    client: AutomationApiClient,
    config: dict,
    result: dict,
    *,
    run_id: str,
    failed: bool = False,
    now: datetime | None = None,
) -> dict:
    preferences = notification_preferences(config)
    classification = classify_result(result, failed=failed)
    decision = {
        "send": True,
        "reason": "enabled",
        "classification": classification,
        "preferences": preferences,
        "quiet_hours_active": quiet_hours_active(preferences, now=now),
    }

    if result.get("notify") is False and classification != "failure":
        return {**decision, "send": False, "reason": "handler_suppressed"}
    if classification == "failure" and not preferences["on_failure"]:
        return {**decision, "send": False, "reason": "failure_notifications_disabled"}
    if classification == "empty" and not preferences["on_empty_result"]:
        return {**decision, "send": False, "reason": "empty_result_notifications_disabled"}
    if classification == "success" and not preferences["on_success"]:
        return {**decision, "send": False, "reason": "success_notifications_disabled"}
    if decision["quiet_hours_active"] and classification != "failure":
        return {**decision, "send": False, "reason": "quiet_hours"}
    if (
        classification == "failure"
        and preferences["collapse_repeated_failures"]
        and has_recent_failure(client, config["id"], run_id, preferences["failure_collapse_window_minutes"], now=now)
    ):
        return {**decision, "send": False, "reason": "repeated_failure_collapsed"}
    return decision


def apply_digest_delivery_quality(config: dict, result: dict, notification: dict | None, decision: dict, *, dry_run: bool) -> dict:
    if config.get("type") != "topic_digest":
        return result
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else None
    if not quality:
        return result
    thresholds = quality.get("thresholds") if isinstance(quality.get("thresholds"), dict) else {}
    if dry_run or not thresholds.get("alert_on_delivery_failure", True):
        return result
    if not decision.get("send"):
        return result
    if notification and notification.get("sent") is True:
        return result

    updated_quality = dict(quality)
    reasons = list(updated_quality.get("reasons") or [])
    reasons.append(
        {
            "code": "discord_delivery_failure",
            "message": "Primary Discord delivery did not report a successful send.",
            "target": (config.get("output") or {}).get("target"),
        }
    )
    updated_quality.update({"status": "failed", "alert_needed": True, "reasons": reasons})
    return {**result, "quality": updated_quality}


def digest_quality_alert_message(config: dict, result: dict, run_id: str, notification: dict | None) -> str:
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    metrics = quality.get("metrics") if isinstance(quality.get("metrics"), dict) else {}
    reasons = quality.get("reasons") if isinstance(quality.get("reasons"), list) else []
    delivery = "n/a"
    if notification:
        delivery = "sent" if notification.get("sent") is True else "not sent"
        if notification.get("target"):
            delivery = f"{delivery} to {notification.get('target')}"
    lines = [
        f"**Brief quality alert: {config.get('name', config.get('id', 'Topic digest'))}**",
        "",
        f"Task: `{config.get('id', 'unknown')}`",
        f"Run: `{run_id}`",
        f"Quality: `{quality.get('status', 'unknown')}`",
        f"Primary delivery: {delivery}",
        "",
        "**Metrics**",
        (
            f"- Items: {metrics.get('item_count', 0)}; sources ok: "
            f"{metrics.get('successful_source_count', 0)}/{metrics.get('processed_source_count', 0)} "
            f"processed ({metrics.get('configured_source_count', 0)} configured)"
        ),
    ]
    empty_sections = metrics.get("empty_sections") if isinstance(metrics.get("empty_sections"), list) else []
    if empty_sections:
        lines.append(f"- Empty sections: {', '.join(str(section) for section in empty_sections[:8])}")
    if reasons:
        lines.extend(["", "**Reasons**"])
        for reason in reasons[:8]:
            code = reason.get("code", "quality_issue") if isinstance(reason, dict) else "quality_issue"
            message = reason.get("message", "") if isinstance(reason, dict) else str(reason)
            lines.append(f"- `{code}`: {message}")
    lines.extend(
        [
            "",
            "**Suggested action**",
            "Inspect the run detail in the local ops UI and pause the task if the issue repeats.",
        ]
    )
    return "\n".join(lines)


def maybe_send_digest_quality_alert(
    client: AutomationApiClient,
    config: dict,
    result: dict,
    *,
    run_id: str,
    dry_run: bool,
    notification: dict | None,
    now: datetime | None = None,
) -> dict | None:
    if config.get("type") != "topic_digest":
        return None
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    if not quality.get("alert_needed"):
        return None
    alert_target = str(quality.get("alert_target") or "alerts")
    alert_config = {**config, "output": {**(config.get("output") or {}), "channel": "discord", "target": alert_target}}
    alert_result = {
        "status": quality.get("status", "degraded"),
        "message": digest_quality_alert_message(config, result, run_id, notification),
        "failed_count": len(quality.get("reasons") or []),
        "notify": True,
    }
    decision = notification_decision(client, alert_config, alert_result, run_id=run_id, failed=True, now=now)
    alert_notification = None
    if decision.get("send"):
        try:
            alert_notification = client.send_discord(
                target=alert_target,
                content=alert_result["message"],
                dry_run=dry_run,
            )
        except Exception as exc:
            alert_notification = {"sent": False, "dry_run": dry_run, "target": alert_target, "error": exc.__class__.__name__}
    return {"decision": decision, "notification": alert_notification}


def topic_digest_observability(
    config: dict,
    result: dict,
    *,
    notification: dict | None,
    decision: dict,
    quality_alert: dict | None,
    dry_run: bool,
) -> dict | None:
    if config.get("type") != "topic_digest":
        return None
    items = result.get("items") if isinstance(result.get("items"), list) else []
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    source_health = result.get("source_health") if isinstance(result.get("source_health"), list) else []
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    metrics = quality.get("metrics") if isinstance(quality.get("metrics"), dict) else {}
    n8n = result.get("n8n") if isinstance(result.get("n8n"), dict) else {}
    output = config.get("output") if isinstance(config.get("output"), dict) else {}

    def metric_int(name: str, fallback: int = 0) -> int:
        try:
            return int(metrics.get(name, fallback) or 0)
        except (TypeError, ValueError):
            return fallback

    processed_sources = metric_int(
        "processed_source_count",
        sum(1 for health in source_health if isinstance(health, dict) and health.get("status") != "blocked"),
    )
    successful_sources = metric_int(
        "successful_source_count",
        sum(1 for health in source_health if isinstance(health, dict) and health.get("status") == "ok"),
    )
    failed_sources = metric_int(
        "failed_source_count",
        sum(1 for health in source_health if isinstance(health, dict) and health.get("status") == "error"),
    )
    blocked_sources = sum(1 for health in source_health if isinstance(health, dict) and health.get("status") == "blocked")
    notification_payload = notification if isinstance(notification, dict) else {}
    alert_notification = (
        quality_alert.get("notification")
        if isinstance(quality_alert, dict) and isinstance(quality_alert.get("notification"), dict)
        else {}
    )
    return {
        "task_id": config.get("id"),
        "result_status": result.get("status"),
        "quality_status": quality.get("status"),
        "quality_alert_needed": quality.get("alert_needed"),
        "summary_mode": result.get("summary_mode"),
        "item_count": metric_int("item_count", len(items)),
        "deduplicated_count": int(result.get("deduplicated_count") or 0),
        "error_count": len(errors),
        "configured_source_count": metric_int("configured_source_count", int(result.get("source_count") or 0)),
        "approved_source_count": int(result.get("approved_source_count") or 0),
        "processed_source_count": processed_sources,
        "successful_source_count": successful_sources,
        "failed_source_count": failed_sources,
        "blocked_source_count": blocked_sources,
        "empty_sections": metrics.get("empty_sections") if isinstance(metrics.get("empty_sections"), list) else [],
        "message_char_count": len(str(result.get("message") or "")),
        "delivery": {
            "channel": output.get("channel"),
            "target": output.get("target"),
            "dry_run": dry_run,
            "decision_send": bool(decision.get("send")),
            "decision_reason": decision.get("reason"),
            "decision_classification": decision.get("classification"),
            "sent": notification_payload.get("sent"),
            "transport": notification_payload.get("transport"),
            "status_code": notification_payload.get("status_code"),
            "error": notification_payload.get("error"),
        },
        "quality_alert_delivery": {
            "sent": alert_notification.get("sent"),
            "target": alert_notification.get("target"),
            "error": alert_notification.get("error"),
        }
        if quality_alert
        else None,
        "n8n": {
            "enabled": bool(config.get("n8n")),
            "status": n8n.get("status"),
            "status_code": n8n.get("status_code"),
            "webhook_id": n8n.get("webhook_id"),
        },
    }


def has_recent_failure(
    client: AutomationApiClient,
    task_id: str,
    current_run_id: str,
    window_minutes: int,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(timezone.utc)
    cutoff = current.astimezone(timezone.utc) - timedelta(minutes=window_minutes)
    try:
        runs = client.list_runs(task_id=task_id, limit=10)
    except Exception:
        return False
    for run in runs:
        if run.get("id") == current_run_id or not run.get("completed_at"):
            continue
        completed_at = parse_datetime(run.get("completed_at"))
        if not completed_at or completed_at < cutoff:
            continue
        if previous_run_failed(run):
            return True
    return False


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def previous_run_failed(run: dict) -> bool:
    if run.get("status") == "failed":
        return True
    log = run.get("log") if isinstance(run.get("log"), dict) else {}
    decision = log.get("notification_decision") if isinstance(log.get("notification_decision"), dict) else {}
    if decision.get("classification") == "failure":
        return True
    result = log.get("result") if isinstance(log.get("result"), dict) else {}
    return classify_result(result) == "failure"


def execute_task(
    client: AutomationApiClient,
    task: dict,
    run_id: str | None = None,
    dry_run_override: bool | None = None,
) -> dict:
    config = task.get("config", task)
    task_id = config["id"]
    dry_run = bool(config.get("runtime", {}).get("dry_run", True))
    if dry_run_override is not None:
        dry_run = dry_run_override
    if run_id is None:
        run = client.queue_run(task_id)
        if run.get("deduplicated"):
            return {
                "task_id": task_id,
                "run_id": run["run_id"],
                "status": run["status"],
                "result": {"status": "deduplicated", "reason": run.get("reason")},
            }
        run_id = run["run_id"]
        claim = client.claim_run(run_id)
        if claim is None:
            return {
                "task_id": task_id,
                "run_id": run_id,
                "status": "claim_conflict",
                "result": {"status": "skipped", "reason": "run already claimed"},
            }
        dry_run = bool(claim.get("dry_run", dry_run))

    effective_config = dict(config)
    effective_runtime = dict(effective_config.get("runtime", {}))
    effective_runtime["dry_run"] = dry_run
    effective_config["runtime"] = effective_runtime

    if not bool(task.get("enabled", config.get("enabled", False))) and not dry_run:
        completed = client.complete_run(
            run_id,
            "skipped_disabled",
            {"task_id": task_id, "reason": "task disabled before live execution"},
        )
        return {"task_id": task_id, "run_id": run_id, "status": completed["status"], "result": {"status": "skipped"}}

    try:
        task_type = effective_config.get("type")
        if task_type == "topic_digest":
            result = run_topic_digest(effective_config)
            result = maybe_run_topic_digest_n8n(effective_config, result, run_id=run_id)
        elif task_type == "server_health":
            result = run_server_health(effective_config)
        elif task_type == "backup_verification":
            result = run_backup_verification(effective_config)
        elif task_type == "printer_supply_status":
            result = run_printer_supply_status(effective_config)
        elif task_type == "n8n_webhook":
            result = run_n8n_webhook(effective_config, run_id=run_id)
        else:
            result = {"status": "skipped", "reason": f"unsupported task type: {task_type}"}

        notification = None
        decision = {"send": False, "reason": "non_discord_output"}
        output = effective_config.get("output", {})
        if output.get("channel") == "discord" and result.get("status") != "skipped":
            decision = notification_decision(client, effective_config, result, run_id=run_id)
        notification_send_error = None
        if decision.get("send"):
            try:
                notification = client.send_discord(
                    target=output["target"],
                    content=result_message(effective_config, result),
                    dry_run=dry_run,
                )
            except Exception as exc:
                notification_send_error = exc
                notification = {"sent": False, "dry_run": dry_run, "target": output.get("target"), "error": exc.__class__.__name__}
        result = apply_digest_delivery_quality(effective_config, result, notification, decision, dry_run=dry_run)
        quality_alert = maybe_send_digest_quality_alert(
            client,
            effective_config,
            result,
            run_id=run_id,
            dry_run=dry_run,
            notification=notification,
        )
        observability = topic_digest_observability(
            effective_config,
            result,
            notification=notification,
            decision=decision,
            quality_alert=quality_alert,
            dry_run=dry_run,
        )

        status = "completed_dry_run" if dry_run else "completed"
        result_quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
        if not dry_run and (notification_send_error or result_quality.get("status") == "failed"):
            status = "failed"
        run_log = {
            "task_id": task_id,
            "result": result,
            "notification": notification,
            "notification_decision": decision,
            "quality_alert": quality_alert,
        }
        if observability is not None:
            run_log["observability"] = observability
        completed = client.complete_run(run_id, status, run_log)
        return {"task_id": task_id, "run_id": run_id, "status": completed["status"], "result": result}
    except Exception as exc:
        output = effective_config.get("output", {})
        failure_result = {"status": "failed", "error": exc.__class__.__name__, "message": str(exc)}
        notification = None
        decision = {"send": False, "reason": "non_discord_output", "classification": "failure"}
        if output.get("channel") == "discord":
            decision = notification_decision(client, effective_config, failure_result, run_id=run_id, failed=True)
            if decision.get("send"):
                try:
                    notification = client.send_discord(
                        target=output["target"],
                        content=failure_message(effective_config, exc),
                        dry_run=dry_run,
                    )
                except Exception as notification_exc:
                    notification = {"sent": False, "error": notification_exc.__class__.__name__}
        client.complete_run(
            run_id,
            "failed",
            {
                "task_id": task_id,
                "error": exc.__class__.__name__,
                "message": str(exc),
                "notification": notification,
                "notification_decision": decision,
            },
        )
        raise


def process_task(client: AutomationApiClient, task: dict) -> dict:
    return execute_task(client, task)


def process_queued_runs(client: AutomationApiClient, tasks: list[dict] | None = None) -> set[str]:
    queued_statuses = {"queued", "queued_dry_run"}
    task_index = {task.get("id"): task for task in tasks or []}
    processed_task_ids: set[str] = set()
    for run in client.list_runs():
        if run.get("completed_at") or run.get("status") not in queued_statuses:
            continue
        task_id = run["task_id"]
        task = task_index.get(task_id) or client.get_task(task_id)
        claim = client.claim_run(run["id"])
        if claim is None:
            continue
        result = execute_task(client, task, run_id=run["id"], dry_run_override=bool(claim.get("dry_run", False)))
        processed_task_ids.add(result["task_id"])
        print(result, flush=True)
    return processed_task_ids


def maybe_run_retention(client: AutomationApiClient, now: float | None = None) -> dict | None:
    global _LAST_RETENTION_RUN_AT
    interval = int(os.getenv("AUTOMATION_RETENTION_INTERVAL_SECONDS", "86400"))
    if interval <= 0:
        return None
    current = time.monotonic() if now is None else now
    if _LAST_RETENTION_RUN_AT is not None and current - _LAST_RETENTION_RUN_AT < interval:
        return None
    _LAST_RETENTION_RUN_AT = current
    return client.run_retention()


def maybe_recover_stale_runs(client: AutomationApiClient, now: float | None = None) -> dict | None:
    global _LAST_STALE_RUN_RECOVERY_AT
    interval = int(os.getenv("AUTOMATION_STALE_RUN_RECOVERY_INTERVAL_SECONDS", "300"))
    if interval <= 0:
        return None
    current = time.monotonic() if now is None else now
    if _LAST_STALE_RUN_RECOVERY_AT is not None and current - _LAST_STALE_RUN_RECOVERY_AT < interval:
        return None
    _LAST_STALE_RUN_RECOVERY_AT = current
    return client.recover_stale_runs()


def run_once() -> None:
    client = AutomationApiClient.from_env()
    client.send_heartbeat(detail={"event": "poll"})
    try:
        recovered = maybe_recover_stale_runs(client)
        if recovered is not None:
            print({"status": "stale_run_recovery", "recovered": recovered.get("recovered_count")}, flush=True)
    except Exception as exc:
        print({"status": "stale_run_recovery_error", "error": exc.__class__.__name__, "message": str(exc)}, flush=True)
    try:
        retention = maybe_run_retention(client)
        if retention is not None:
            print({"status": "retention", "deleted": retention.get("deleted")}, flush=True)
    except Exception as exc:
        print({"status": "retention_error", "error": exc.__class__.__name__, "message": str(exc)}, flush=True)
    tasks = client.list_tasks()
    queued_task_ids = process_queued_runs(client, tasks)
    for task in due_tasks(tasks):
        if task.get("id") in queued_task_ids:
            continue
        result = process_task(client, task)
        print(result, flush=True)


def main() -> None:
    interval = int(os.getenv("WORKER_POLL_SECONDS", "60"))
    while True:
        try:
            run_once()
        except Exception as exc:
            print({"status": "worker_error", "error": exc.__class__.__name__, "message": str(exc)}, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
