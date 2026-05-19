from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from worker.scheduler import due_tasks, is_task_due


def scheduled_task(enabled: bool = True):
    return {
        "enabled": enabled,
        "config": {
            "trigger": {"kind": "schedule", "cron": "0 8 * * 1-5", "timezone": "Europe/Berlin"}
        },
    }


def test_scheduler_identifies_due_enabled_tasks():
    now = datetime(2026, 5, 18, 8, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert is_task_due(scheduled_task(), now=now) is True
    assert due_tasks([scheduled_task()], now=now)


def test_disabled_tasks_are_ignored():
    now = datetime(2026, 5, 18, 8, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert is_task_due(scheduled_task(enabled=False), now=now) is False
    assert due_tasks([scheduled_task(enabled=False)], now=now) == []
