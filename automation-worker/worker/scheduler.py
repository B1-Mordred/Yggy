from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter


def is_task_due(task: dict, now: datetime | None = None) -> bool:
    if not task.get("enabled", False):
        return False
    config = task.get("config", task)
    trigger = config.get("trigger", {})
    if trigger.get("kind", "schedule") != "schedule":
        return False
    cron = trigger.get("cron")
    timezone_name = trigger.get("timezone", "Europe/Berlin")
    if not cron or not croniter.is_valid(cron):
        return False
    local_now = now or datetime.now(ZoneInfo(timezone_name))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        local_now = local_now.astimezone(ZoneInfo(timezone_name))
    return croniter.match(cron, local_now)


def due_tasks(tasks: list[dict], now: datetime | None = None) -> list[dict]:
    return [task for task in tasks if is_task_due(task, now=now)]
