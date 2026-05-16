# Operations

## Daily Use

1. Ask yggdrasil to draft an automation.
2. Review the generated task config.
3. The automation API validates and stores the task disabled.
4. Approve through the local CLI or local UI using the admin key.
5. The worker executes only approved enabled tasks.

## Pause A Task

```bash
python scripts/pause_task.py --task-id daily_local_ai_security_briefing
```

L2+ pauses require the admin key.

## Approve A Task

```bash
python scripts/approve_task.py --approval-id <id> --nonce <nonce>
```

Never paste `AUTOMATION_ADMIN_API_KEY` into Open WebUI, Hermes, chat, Knowledge, task YAML, or logs.

## Logs

Run logs are stored through the API with secret-looking values redacted. Treat logs as potentially sensitive operational data.

## Run Locking

Manual and scheduled task runs use a guarded lifecycle:

```text
queued -> running -> completed
queued_dry_run -> running_dry_run -> completed_dry_run
```

The API will not create a second active run for the same task while a queued or running run exists. Live runs are also deduplicated for `AUTOMATION_RUN_DEDUPE_SECONDS` after completion, defaulting to 300 seconds, to avoid accidental repeated Discord sends. Only the admin key may force a new live run during that cooldown, and force does not bypass an already active run.

## Retention Cleanup

The worker periodically calls the API retention endpoint with the worker key. The model-facing tool key cannot run cleanup.

Default retention:

```text
AUTOMATION_RUN_RETENTION_DAYS=30
AUTOMATION_AUDIT_RETENTION_DAYS=90
AUTOMATION_TEMP_TASK_RETENTION_HOURS=24
AUTOMATION_RETENTION_INTERVAL_SECONDS=86400
```

Cleanup removes only completed old runs, old audit events, and disabled temporary tasks whose ids start with `temporary_` or `test_`. Active/running runs and normal task ids are preserved.

Admin preview:

```bash
curl -sS -X POST http://127.0.0.1:8088/maintenance/retention \
  -H "X-Automation-Api-Key: ${AUTOMATION_ADMIN_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

Admin one-off cleanup with defaults:

```bash
curl -sS -X POST http://127.0.0.1:8088/maintenance/retention \
  -H "X-Automation-Api-Key: ${AUTOMATION_ADMIN_API_KEY}"
```
