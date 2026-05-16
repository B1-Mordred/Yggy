from __future__ import annotations

import os
import time

from worker.clients.automation_api import AutomationApiClient
from worker.handlers.server_health import run_server_health
from worker.handlers.topic_digest import run_topic_digest
from worker.scheduler import due_tasks


def result_message(config: dict, result: dict) -> str:
    if result.get("message"):
        return str(result["message"])
    return f"{config.get('name', config.get('id', 'Automation task'))}: {result}"


def process_task(client: AutomationApiClient, task: dict) -> dict:
    config = task.get("config", task)
    task_id = config["id"]
    run = client.queue_run(task_id)
    run_id = run["run_id"]
    dry_run = bool(config.get("runtime", {}).get("dry_run", True))

    try:
        task_type = config.get("type")
        if task_type == "topic_digest":
            result = run_topic_digest(config)
        elif task_type == "server_health":
            result = run_server_health(config)
        else:
            result = {"status": "skipped", "reason": f"unsupported task type: {task_type}"}

        notification = None
        output = config.get("output", {})
        if output.get("channel") == "discord" and result.get("status") != "skipped":
            notification = client.send_discord(
                target=output["target"],
                content=result_message(config, result),
                dry_run=dry_run,
            )

        status = "completed_dry_run" if dry_run else "completed"
        completed = client.complete_run(
            run_id,
            status,
            {"task_id": task_id, "result": result, "notification": notification},
        )
        return {"task_id": task_id, "run_id": run_id, "status": completed["status"], "result": result}
    except Exception as exc:
        client.complete_run(
            run_id,
            "failed",
            {"task_id": task_id, "error": exc.__class__.__name__, "message": str(exc)},
        )
        raise


def run_once() -> None:
    client = AutomationApiClient.from_env()
    tasks = client.list_tasks()
    for task in due_tasks(tasks):
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
