from __future__ import annotations

import os
import time

from worker.clients.automation_api import AutomationApiClient
from worker.handlers.server_health import run_server_health
from worker.handlers.topic_digest import run_topic_digest
from worker.scheduler import due_tasks


def run_once() -> None:
    client = AutomationApiClient.from_env()
    tasks = client.list_tasks()
    for task in due_tasks(tasks):
        config = task.get("config", task)
        task_type = config.get("type")
        if task_type == "topic_digest":
            result = run_topic_digest(config)
        elif task_type == "server_health":
            result = run_server_health(config)
        else:
            result = {"status": "skipped", "reason": f"unsupported task type: {task_type}"}
        client.queue_run(config["id"])
        print({"task_id": config["id"], "result": result})


def main() -> None:
    interval = int(os.getenv("WORKER_POLL_SECONDS", "60"))
    while True:
        run_once()
        time.sleep(interval)


if __name__ == "__main__":
    main()
