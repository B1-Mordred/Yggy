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

## Local Operations Dashboard

The API serves a read-only dashboard at:

```text
http://127.0.0.1:8088/ops
```

It shows task state, latest runs, pending approvals, worker heartbeat, and retention status. It does not expose secrets, does not include raw run logs, and is not included in the OpenAPI tool schema.

Configure it with separate local credentials:

```text
AUTOMATION_OPS_DASHBOARD_ENABLED=true
AUTOMATION_OPS_DASHBOARD_USER=admin
AUTOMATION_OPS_DASHBOARD_PASSWORD=replace-with-long-random-dashboard-password
```

By default Compose publishes the API only on localhost:

```text
127.0.0.1:8088
```

To make the dashboard reachable from a trusted LAN while keeping localhost access for local services, set the host's LAN address:

```text
AUTOMATION_API_LAN_PUBLISHED_HOST=192.168.2.2
AUTOMATION_API_LAN_PUBLISHED_PORT=8088
```

Deploy with the LAN override:

```bash
docker compose -f docker-compose.automation.yml -f docker-compose.lan.yml up -d automation-api
```

Then use:

```text
http://192.168.2.2:8088/ops
```

LAN exposure publishes the whole automation API port, not only the dashboard. The dashboard still requires Basic auth, and write/API endpoints still require API keys, but `/health`, `/docs`, and `/openapi.json` are reachable on that interface. Do not expose this port to untrusted networks or the public internet.
