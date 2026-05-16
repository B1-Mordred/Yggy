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
        elif task_type == "server_health":
            result = run_server_health(effective_config)
        else:
            result = {"status": "skipped", "reason": f"unsupported task type: {task_type}"}

        notification = None
        output = effective_config.get("output", {})
        if output.get("channel") == "discord" and result.get("status") != "skipped" and result.get("notify", True):
            notification = client.send_discord(
                target=output["target"],
                content=result_message(effective_config, result),
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


def run_once() -> None:
    client = AutomationApiClient.from_env()
    client.send_heartbeat(detail={"event": "poll"})
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
