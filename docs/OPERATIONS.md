# Operations

## Daily Use

1. Ask Bragi when you want to speak naturally.
2. Bragi turns supported requests into a canonical intent and asks for user confirmation when it would create or change an automation.
3. Heimdal, inside the automation API, validates the intent against `configs/capabilities.yaml`.
4. Accepted intents are forwarded to strict Yggdrasil as deterministic canonical actions.
5. The automation API validates and stores the task disabled.
6. Approve through the local CLI or local approval UI.
7. The worker executes only approved enabled tasks.

Use Yggdrasil directly only for deterministic control-plane commands such as
listing tasks, showing known task details, requesting approval, pausing tasks,
or running an already approved task. Yggdrasil is intentionally not the friendly
interpreter; Bragi is the friendly one, with just enough sarcasm to remind you
that automation is still mostly a way to create faster paperwork.

The first Bragi/Heimdal milestone accepts only these capabilities:

```text
server_health.v1
topic_digest.v1
printer_supply_status.v1
n8n_webhook.v1
```

Unsupported or unsafe natural requests are not forwarded to Yggdrasil. Useful
but unsupported requests should become a new capability proposal, not a forced
match to an existing task type.

Use the configuration registry scripts to compare Git YAML with live MySQL-backed
API state before and after operational changes:

```bash
python scripts/export_live_configs.py --clean
python scripts/diff_registry.py --no-fail-on-drift
```

See `docs/CONFIG_REGISTRY.md` for the full workflow.

For common task shapes, use the template catalog instead of copying a previous
task by hand:

```bash
python scripts/list_task_templates.py
python scripts/render_task_template.py topic_digest \
  --id draft_local_ai_weekday_briefing \
  --name "Draft Local AI Weekday Briefing" \
  --out configs/tasks/draft_local_ai_weekday_briefing.yaml
python scripts/validate_configs.py
```

Rendered tasks are disabled and dry-run by default. They still require import,
review, approval, and enablement through the normal control-plane workflow. See
`docs/TASK_TEMPLATES.md`.

Existing enabled tasks should be changed through task change proposals rather
than direct model-facing updates:

```bash
python scripts/propose_task_change.py \
  --task-id daily_local_ai_security_briefing \
  --proposed-config /path/to/proposed-task.yaml \
  --summary "Move the weekday briefing to 07:30"
python scripts/approve_task_change.py --proposal-id <proposal-id> --nonce <nonce> --apply
```

Yggdrasil can create schedule-change proposals and list pending proposals, but
approve/apply remains local-admin only. See `docs/TASK_CHANGE_PROPOSALS.md`.

The daily local AI/security briefing is an approved L1 notification task. It is
scheduled for weekdays at 08:00 Europe/Berlin and sends to the whitelisted
`briefings` Discord target. Its source list is restricted to enabled entries in
`configs/sources/approved_sources.yaml`; broad `web_query` sources are blocked
for topic digests by policy.

The worker re-checks that registry during execution. Unapproved, disabled, or
identity-mismatched sources are marked blocked and are not fetched. Digest run
logs include `source_health`, `approved_source_count`, and per-item trust
metadata so source failures are audit-visible without trusting source content as
instructions.

## Local Metrics Exporter

Yggy includes an internal-only `metrics-exporter` service for read-only service
health visibility. It reads:

```text
configs/metrics/services.yaml
```

and exposes:

```text
GET http://metrics-exporter:8090/health
GET http://metrics-exporter:8090/metrics/services
```

The exporter performs only bounded HTTP GET checks against the configured
allowlist. It does not mount the Docker socket, run shell commands, use host
network mode, inspect containers, write host files, or expose credentials. The
`morning_server_health_check` task reads the exporter through a `service_metrics`
check and can alert on failed configured services.

## Printer Supply Checks

Yggy includes a bounded `printer_supply_status` task type for read-only supply
monitoring. It reads only printer IDs configured in:

```text
configs/printers/printers.yaml
```

The first implementation supports `http_json` endpoints served by the internal
`printer-status-exporter` service. That exporter is configured in:

```text
configs/printer-status-exporter/printers.yaml
```

and exposes:

```text
GET http://printer-status-exporter:8091/health
GET http://printer-status-exporter:8091/printers
GET http://printer-status-exporter:8091/printers/<printer-id>/supplies
```

It can serve static dry-run sample data or perform one bounded HTTP GET against
an operator-configured upstream URL. It does not scan the LAN, use SNMP
directly, submit print jobs, administer printers, or store printer credentials.

Expected endpoint shape:

```json
{
  "supplies": [
    {"name": "Black toner", "level_percent": 75},
    {"name": "Cyan toner", "percent": "18%"}
  ]
}
```

The worker also accepts:

```json
{"supplies": {"black": 75, "cyan": "18%"}}
```

Render a disabled dry-run task with:

```bash
python scripts/render_task_template.py printer_supply_status \
  --id daily_printer_supply_status \
  --name "Daily Printer Supply Status" \
  --printer-id printer_status_exporter_example \
  --low-threshold-percent 20
```

Replace the example exporter source and printer registry entry with a real
read-only endpoint before enabling a live task. Keep the registry URL pointed at
the internal exporter endpoint, not at arbitrary chat-provided URLs.

Recommended real-printer workflow:

1. Configure both printer registries with the helper script.

For a real read-only adapter:

```bash
python scripts/configure_printer_status.py \
  --printer-id office_laser \
  --name "Office Laser" \
  --upstream-url http://printer-adapter.local/supplies \
  --threshold 20
```

For a static dry-run entry:

```bash
python scripts/configure_printer_status.py \
  --printer-id office_laser_dry_run \
  --name "Office Laser Dry Run" \
  --static-supply "Black toner=75" \
  --static-supply "Cyan toner=64"
```

Use `--dry-run` to preview without writing files and `--force` to update an
existing printer ID. The helper writes:

```text
configs/printer-status-exporter/printers.yaml
configs/printers/printers.yaml
```

The approved registry URL always points at:

```text
http://printer-status-exporter:8091/printers/<printer-id>/supplies
```

2. Validate the registry mapping:

```bash
python scripts/validate_printer_status.py
```

3. If the exporter is running and you are inside a container/network that can
   resolve `printer-status-exporter`, perform the bounded live check:

```bash
python scripts/validate_printer_status.py --live
```

From the host, use Docker Compose to query through the worker network rather
than publishing the exporter port:

```bash
docker compose -f docker-compose.automation.yml exec -T automation-worker python - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://printer-status-exporter:8091/printers/<printer-id>/supplies", timeout=5) as r:
    print(json.dumps(json.load(r), indent=2))
PY
```

Do not use Bragi or Yggdrasil to discover printer IPs or guess printer admin
URLs. Treat adding a printer source as an operator configuration task.

## Backup Verification

Yggy includes a first-class `backup_verification` task type for read-only backup
health checks. The worker mounts only:

```text
./backups:/app/backups:ro
```

and runs with `YGGY_WORKER_UID:YGGY_WORKER_GID` so it can read local backup
directories created by the backup script without granting write access. The task
schema rejects backup roots outside `/app/backups`. The handler does not run
`scripts/restore_yggy.sh`, `docker`, `mysql`, or arbitrary shell commands.
Instead, it performs the restore dry-run checks directly:

- newest `yggy-*` backup exists
- backup age is below the configured threshold
- `manifest.json` parses and does not claim embedded secrets
- required API export, OpenAPI, git commit, and MySQL dump files exist
- MySQL dump is large enough and contains a dump header
- bounded secret-marker scan reports paths and match counts only

Example task:

```text
configs/tasks/example_backup_verification.yaml
```

The example sends to the whitelisted `alerts` Discord target only on anomalies
and starts disabled/dry-run until approved. Manual restore remains an
operator-controlled procedure through:

```bash
scripts/restore_yggy.sh --backup-dir backups/yggy-YYYYmmdd-HHMMSSZ
```

Notification preferences are stored in each task config. The worker records a
`notification_decision` in every run log so you can see whether a message was
sent, skipped for quiet hours, skipped because the result was empty, or collapsed
as a repeated failure.

## Pause A Task

```bash
python scripts/pause_task.py --task-id daily_local_ai_security_briefing
```

L2+ pauses require the admin key.

## Approve A Task

Use the local approval UI:

```text
https://yggy.b1.germering:8443/ops
```

The UI uses the dashboard username/password, shows pending approval details,
and asks for the approval nonce. It does not expose the admin API key to the
browser. Mutating approval actions are hidden from OpenAPI and require the
dashboard credentials plus a same-origin action header.

CLI approval is still available for local shell use:

```bash
python scripts/approve_task.py --approval-id <id> --nonce <nonce>
```

Never paste `AUTOMATION_ADMIN_API_KEY` into Open WebUI, Hermes, a browser form,
chat, Knowledge, task YAML, or logs.

## Notify Pending Approvals

Approval notifications can be sent to the whitelisted Discord `approvals`
target without granting Discord approval authority:

```bash
python scripts/notify_pending_approvals.py --dry-run --all
python scripts/notify_pending_approvals.py --approval-id <approval-id>
python scripts/notify_pending_task_changes.py --dry-run --all
python scripts/notify_pending_task_changes.py --proposal-id <proposal-id>
```

Live approval sends require either `--approval-id` or `--all`; live task-change
sends require either `--proposal-id` or `--all`. Messages include ids, task,
risk, and redacted diff summaries, but not the admin key, nonce hash, approval
nonce, or proposal nonce. Approve only through the local `/ops` UI or local CLI.
See `docs/APPROVAL_NOTIFICATIONS.md`.

## Logs

Run logs are stored through the API with secret-looking values redacted. Treat logs as potentially sensitive operational data.

Useful notification-decision reasons:

```text
enabled
handler_suppressed
success_notifications_disabled
failure_notifications_disabled
empty_result_notifications_disabled
quiet_hours
repeated_failure_collapsed
```

## n8n Webhook Backend

n8n webhooks are approved execution backends. The automation API validates any
task with an `n8n:` block against `configs/n8n/webhooks.yaml`, and the worker
calls only those internal paths. Live dispatch requires
`N8N_WEBHOOK_SHARED_SECRET`; dry-run dispatch records the intended payload shape
but does not call n8n.

The n8n workflow should also authenticate the inbound webhook before running any
workflow body. The starter workflow uses n8n Webhook Header Auth with a
credential named `Yggy Webhook Header Auth`; that credential stores the
`X-Yggy-Webhook-Token` value in n8n's credential store, not in Git or YAML.
The current starter workflow performs only an internal payload normalization and
returns a bounded JSON response to the worker. The worker records that response
with secret-like keys redacted.

For `topic_digest` tasks, n8n normalization is an optional post-processing step:
the worker builds a bounded payload from the already-created digest, sends it to
the approved n8n normalizer, stores the normalized response in the run log, and
then applies Yggy's normal notification policy. Discord delivery remains owned by
Yggy, not n8n.

Example dry-run task:

```text
configs/tasks/example_n8n_webhook.yaml
```

Approved webhook registry:

```text
configs/n8n/webhooks.yaml
```

## Run Locking

Manual and scheduled task runs use a guarded lifecycle:

```text
queued -> running -> completed
queued_dry_run -> running_dry_run -> completed_dry_run
```

The API will not create a second active run for the same task while a queued or running run exists. Live runs are also deduplicated for `AUTOMATION_RUN_DEDUPE_SECONDS` after completion, defaulting to 300 seconds, to avoid accidental repeated Discord sends. Only the admin key may force a new live run during that cooldown, and force does not bypass an already active run.

Each task can also define explicit run safety limits under `policy`:

```yaml
max_runs_per_hour: 3
max_runs_per_day: 10
min_seconds_between_runs: 300
```

These limits apply to manual and scheduled queues before a run row is created.
Denied attempts are written to the audit log as `task.run.denied`. The response
uses `status: rate_limited`, includes the reason and retry-after estimate, and
does not call the worker, Discord, n8n, or source fetchers.

When the worker claims a queued run, the API records a lease in the run log. The
worker periodically calls `/maintenance/stale-runs` with the worker key. Expired
`running` or `running_dry_run` leases are marked `failed_stale` or
`failed_stale_dry_run`, audited as `run.stale_recovered`, and no longer block a
new run for that task. The default lease is:

```text
AUTOMATION_RUN_LEASE_SECONDS=1800
AUTOMATION_STALE_RUN_RECOVERY_INTERVAL_SECONDS=300
```

## Retention Cleanup

The worker periodically calls the API retention endpoint with the worker key. The model-facing tool key cannot run cleanup.

Default retention:

```text
AUTOMATION_RUN_RETENTION_DAYS=30
AUTOMATION_AUDIT_RETENTION_DAYS=90
AUTOMATION_TEMP_TASK_RETENTION_HOURS=24
AUTOMATION_RETENTION_INTERVAL_SECONDS=86400
```

Cleanup removes only completed old runs, old audit events, disabled temporary
tasks whose ids start with `temporary_` or `test_`, and config-version snapshots
belonging to those temporary tasks. Active/running runs and normal task ids are
preserved.

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

The API serves a local operations and approval dashboard at:

```text
http://127.0.0.1:8088/ops
```

It is split into views for overview, tasks, runs, task-change proposals,
capability proposals, approvals, audit, and retention so routine checks do not
require scanning every table. It shows task state, latest runs, pending reviews,
worker heartbeat, and retention status.
The task view includes browser-side filters for quick narrowing by text, state,
and type. Runs, proposals, approvals, and audit use hidden server-side endpoints
for filtering and pagination so larger queues can be narrowed without exposing
dashboard-only routes in the OpenAPI tool schema. Each operational list has a
configurable `Per page` control. The API enforces a minimum page size of `5` and
a maximum of `100`; the browser remembers the selected page size and filter
values locally. Click sortable table headers to reorder task, run, and audit
views. Task sorting happens in the browser; run and audit sorting is handled by
hidden server-side endpoints with allowlisted sort fields only. The header
includes a compact saved-view selector for common checks: failed runs, pending
approvals, pending task-change proposals, pending capability proposals, recent
Discord sends, task changes, and worker activity. Selecting a saved view applies
the relevant filters, sort order, and dashboard tab without adding any
model-facing capability.
Pending approvals include actions, worst-case failure mode, and the redacted task
config. The dashboard can approve or reject pending approvals when the operator
enters the approval nonce. It does not expose secrets, does not expose nonce
hashes, and is not included in the OpenAPI tool schema.

Task ids in the dashboard are clickable. The task-detail panel is backed by the
hidden `/ops/tasks/{task_id}` endpoint and shows a bounded, redacted projection
of the task config, recent approval history, recent runs, and server-computed
action eligibility for dry-run, live-run, pause, and resume. It also shows
redacted config version snapshots and structured diffs so proposed changes can
be reviewed as before/after field changes without widening the task table.
Approval history in this panel excludes nonce hashes and any operator secrets.

Pending approvals include the config version linked to that approval request
when one exists. The dashboard displays the structured diff for that approval so
the operator can review the proposed config change before entering the approval
nonce. Config snapshots are stored redacted and are not exposed in the OpenAPI
tool schema.

Disabled draft, rejected, pending-approval, or paused tasks can be archived
through the admin-only API:

```bash
curl -sS -X POST http://127.0.0.1:8088/tasks/<task-id>/archive \
  -H "X-Automation-Api-Key: $AUTOMATION_ADMIN_API_KEY"
```

Archive is not deletion. The task is disabled, pending approvals for that task
are rejected, a config version is recorded, and a `task.archive` audit event is
kept. Archived tasks are hidden from `GET /tasks` by default and can be reviewed
with:

```bash
curl -sS "http://127.0.0.1:8088/tasks?include_archived=true" \
  -H "X-Automation-Api-Key: $AUTOMATION_ADMIN_API_KEY"
```

The `Proposals` view contains task-change proposals created through
`POST /tasks/{task_id}/propose-change`. It can filter by text, task id,
requester, approval level, status, and risk severity. Pending proposals can be
approved with the proposal nonce, approved proposals can be applied, and pending
or approved proposals can be rejected. These actions are local-only hidden
`/ops/task-change-proposals` endpoints, require dashboard access plus the
`X-Yggy-Ops-Action: task-change-proposal` same-origin action header, and are not
included in the OpenAPI tool schema. The `Approvals` view remains reserved for
pending approvals that are not config proposals.

The `Capabilities` view contains useful-but-unsupported capability proposals
created through `POST /capability-proposals/draft`, usually by Bragi after it
decides a request is reasonable but not executable by any registered capability.
It can filter by text, requester, source channel, likely approval level, and
status. Pending proposals can be accepted for implementation review, rejected,
or closed. These actions are hidden `/ops/capability-proposals` endpoints,
require dashboard access plus `X-Yggy-Ops-Action: capability-proposal`, and are
not included in the OpenAPI tool schema. Accepting a capability proposal is only
backlog state. It does not create a task, approval, run, worker action, or
Yggdrasil request, and there is intentionally no apply button.

Accepted capability proposals can be moved to `implementation_planned` from the
same view. The generated implementation plan lists expected registry, template,
worker, test, and documentation changes plus required operator decisions,
security boundaries, and acceptance tests. This planning step is also
non-executable. It does not create or modify a task and does not tell
Yggdrasil to do anything. A planned capability can be marked `superseded`, or
marked `implemented` only after the capability is actually present in the
registered capability catalog.

Prior config versions can be reverted from the task-detail panel. A revert does
not immediately enable or run the task. It creates a new disabled
`revert_draft` version from the selected snapshot, sets the task to
`pending_approval`, records `task.config.revert` in the audit log, and creates a
fresh approval request. The new approval nonce is shown once in the local
dashboard response for the operator. The task remains disabled until that
approval is accepted through the local approval flow.

Recent run ids in the dashboard are clickable. The run list is backed by hidden
`/ops/runs` with filters for text, task id, status, and notification sent/not
sent. Runs can be sorted by run id, task id, status, created time, or completed
time. The `Recent Discord sends` saved view uses the notification-sent filter
and newest-first sorting. The Runs view also renders a `Run Timeline` panel from
the current filtered page, so failures, dry-runs, and Discord sends are visible
as a sequence in the current sort order. Above that timeline, the dashboard shows
summary counts for the full filtered run set: total, success, failure, dry-run,
Discord sent, and last failure time. The task-detail panel includes a `Timeline`
button that switches to the Runs view with that task id applied as a filter. The
run-detail panel is backed by the hidden `/ops/runs/{run_id}` endpoint and shows
a bounded, redacted projection of the run: topic digest message and items, n8n
normalizer response, notification decision, and Discord send result. It
intentionally does not expose raw logs, API keys, approval nonces, webhook
secrets, or dashboard credentials.

Task rows include manual run controls:

```text
Dry run
Live run
Pause
Resume
```

`Dry run` queues `queued_dry_run` and overrides the task runtime for that single
run so external delivery remains suppressed by the worker. `Live run` queues a
live run only for enabled L0/L1 tasks. L2+ live runs still require the admin API
or a narrower future approval flow, and L4 remains manual-only. Manual run
actions use the same active-run lock and recent-live dedupe window as the
OpenAPI task run endpoint.

`Pause` disables an enabled L0/L1 task and mirrors `enabled: false` into the
stored task config. `Resume` re-enables L0 tasks directly and re-enables L1
tasks only when an approved approval record already exists for the same task and
approval level. Pending, rejected, L2+, L3, and L4 tasks are not resumed through
the dashboard.

The `Audit` view is backed by hidden `/ops/audit` and lists recent audit events
for approvals, task drafts/updates, manual runs, pause/resume, run lifecycle
updates, heartbeats, and retention cleanup. Audit details are bounded and
redacted before reaching the browser. Audit filters are server-backed and can
narrow by actor role, action, resource type, resource id, or text across the
audit event metadata. Audit filters do not search raw secret-bearing detail
payloads. Audit pagination is server-side. Audit events can be sorted by time,
actor, action, resource type, or resource id.

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

### LAN Firewall Scope

Use UFW to restrict the published API/dashboard port to trusted LAN clients. The helper defaults to a dry-run:

```bash
scripts/configure_lan_firewall.sh --lan-cidr 192.168.2.0/24
```

Apply the port-specific rule set and enable UFW:

```bash
scripts/configure_lan_firewall.sh --apply --enable-ufw --lan-cidr 192.168.2.0/24
```

The default script mode preserves existing inbound services by setting UFW's incoming policy to `allow` and then adding explicit rules for port `8088`:

```text
allow 8088/tcp from 192.168.2.0/24
deny 8088/tcp from anywhere else
```

For stricter per-device access, use a single-client CIDR such as:

```bash
scripts/configure_lan_firewall.sh --apply --enable-ufw --lan-cidr 192.168.2.25/32
```

Use `--default-deny-incoming` only after adding allow rules for every other service that must remain reachable.

## HTTPS Dashboard Proxy

Technitium uses ports `80` and `443` on this host, so Yggy HTTPS is exposed on a dedicated LAN port:

```text
https://yggy.b1.germering:8443/ops
```

The HTTPS proxy is a Caddy container that joins the internal automation network and proxies to `automation-api:8088`. It uses Caddy's internal CA, so the connection is encrypted but browsers will warn until the Caddy local root CA is trusted on the client device.

Configure:

```text
YGGY_HTTPS_HOST=yggy.b1.germering
YGGY_HTTPS_PUBLISHED_HOST=192.168.2.2
YGGY_HTTPS_PUBLISHED_PORT=8443
YGGY_HTTPS_ALLOWED_CIDR=192.168.2.0/24
```

Deploy:

```bash
docker compose -f docker-compose.automation.yml -f docker-compose.https.yml up -d yggy-https-proxy
```

Add or update the Technitium DNS record:

```bash
scripts/configure_technitium_yggy_dns.sh --apply
```

Scope the HTTPS port in UFW:

```bash
scripts/configure_lan_firewall.sh --apply --enable-ufw --lan-cidr 192.168.2.0/24 --port 8443
```

After the proxy is verified, direct LAN access to `8088` can be removed by deploying without `docker-compose.lan.yml` and keeping only `127.0.0.1:8088` plus `192.168.2.2:8443`.

Export the Caddy local root CA if you want to trust it on LAN browsers:

```bash
docker cp yggy-https-proxy:/data/caddy/pki/authorities/local/root.crt ./yggy-caddy-root.crt
```

Install that certificate as a trusted root CA only on devices you control.

## Backups

Create a local Yggy backup:

```bash
scripts/backup_yggy.sh
```

Backups are written under `backups/`, which is ignored by Git. They include MySQL state, redacted API exports, OpenAPI, compose source files, and git metadata. Compose files are copied without resolving `.env`, so secrets are not expanded into the backup. They do not include `.env`, API keys, Discord tokens, dashboard passwords, or Caddy private keys.

Restore is dry-run by default:

```bash
scripts/restore_yggy.sh --backup-dir backups/yggy-YYYYmmdd-HHMMSSZ
```

See `docs/BACKUP_RESTORE.md` before applying a restore.
