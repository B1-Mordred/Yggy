# Task Schema

Task YAML is declarative and non-secret.

```yaml
id: daily_local_ai_security_briefing
name: Daily Local AI Security Briefing
type: topic_digest
enabled: true
owner: local_user
created_by: yggdrasil

trigger:
  kind: schedule
  cron: "0 8 * * 1-5"
  timezone: Europe/Berlin

sources:
  - source_id: open_webui_releases
    type: rss
    url: https://github.com/open-webui/open-webui/releases.atom
  - source_id: ollama_releases
    type: rss
    url: https://github.com/ollama/ollama/releases.atom

filters:
  include:
    - Open WebUI
    - Ollama
  exclude:
    - sponsored

output:
  channel: discord
  target: briefings
  format: "5 bullets, impact, source links, recommended action"

policy:
  approval_level: L1_NOTIFY_ONLY
  max_items: 10
  require_sources: true
  max_runs_per_hour: 3
  max_runs_per_day: 10
  min_seconds_between_runs: 300
  allow_external_side_effects: false
  allow_shell: false
  allow_docker_socket: false
  allow_filesystem_write: false

runtime:
  dry_run: false
  timeout_seconds: 120
  retry_count: 1

notifications:
  on_success: true
  on_failure: true
  on_empty_result: false
  quiet_hours:
    enabled: true
    start: "22:00"
    end: "07:00"
    timezone: Europe/Berlin
  collapse_repeated_failures: true
  failure_collapse_window_minutes: 360
```

## Validation Rules

The API rejects tasks when:

- `id` is missing or not slug-like
- cron is invalid
- timezone is invalid
- `policy.approval_level` is missing
- `policy.max_runs_per_hour`, `policy.max_runs_per_day`, or
  `policy.min_seconds_between_runs` is outside the allowed range
- `allow_shell` is true
- `allow_docker_socket` is true
- secret-looking values appear in plain text
- external side effects are requested below L3
- filesystem writes are requested below L2
- Discord target is not whitelisted
- source URL scheme is not `http` or `https`
- topic digest sources are not listed in `configs/sources/approved_sources.yaml`
- topic digest sources omit `source_id`
- topic digest sources use `web_query` while source policy disables web queries
- notification quiet-hour times are not `HH:MM`
- notification quiet-hour timezone is invalid
- `failure_collapse_window_minutes` is outside the allowed range
- `n8n_webhook` tasks omit `n8n.webhook_id`
- `n8n_webhook` tasks reference a webhook that is not listed in `configs/n8n/webhooks.yaml`
- `n8n_webhook` paths are absolute URLs or do not start with `/webhook/` or `/webhook-test/`
- `n8n_webhook` method is not `POST`

## Approved Sources

`configs/policies.yaml` points to `configs/sources/approved_sources.yaml`. The
source registry is the allowlist for topic digest inputs. A task source must name
an enabled `source_id`, and the configured URL/query must match that registry
entry.

Approved source entries include:

- `trust_level`: a slug-like label such as `official_project_release_feed`.
- `categories`: slug-like tags used for review and later routing.
- `enabled`: disabled sources are rejected by the API and blocked by the worker.
- `max_items`: optional per-source cap within the task-level `policy.max_items`.

The worker enforces the same registry before fetching. Run logs include
`source_health` records with `ok`, `error`, or `blocked` status, plus
`approved_source_count`. Each digest item includes `source_id`, `source_name`,
`source_trust_level`, and `source_categories`.

This keeps source selection declarative and reviewable. Webpages, feeds, release
notes, and other retrieved content remain untrusted data; they do not gain command
authority by being approved as data sources.

## Notification Preferences

Task notifications are declarative and evaluated by the worker after a bounded
handler completes or fails:

- `on_success`: send normal successful results.
- `on_failure`: send failed/degraded results and handler exceptions.
- `on_empty_result`: send otherwise successful digests with no matching items.
- `quiet_hours`: suppress non-failure notifications during the configured local
  time window. Failure notifications still pass through quiet hours.
- `collapse_repeated_failures`: suppress repeated failure notifications when a
  previous failure for the same task completed inside
  `failure_collapse_window_minutes`.

Every run log includes a `notification_decision` object with the classification
and suppression reason.

## Server Health Checks

`server_health` tasks can check bounded HTTP endpoints. Supported check types
include:

- `http_health`: status-code health check.
- `worker_heartbeat`: automation API health plus worker heartbeat freshness.
- `ollama_tags`: Ollama model inventory check.
- `service_metrics`: internal metrics exporter summary from
  `http://metrics-exporter:8090/metrics/services`.

The `service_metrics` check reports failed configured services from the
metrics-exporter allowlist without exposing Docker, process, filesystem, or
secret data.

## Printer Supply Tasks

`printer_supply_status` tasks check read-only supply endpoints from the approved
printer registry. In the default deployment those endpoints are served by the
internal `printer-status-exporter`, which reads
`configs/printer-status-exporter/printers.yaml` and exposes normalized supply
JSON under `http://printer-status-exporter:8091/printers/<id>/supplies`.

A task must include at least one `printer_supplies` entry:

```yaml
type: printer_supply_status
printer_supplies:
  - printer_id: printer_status_exporter_example
    name: Printer Status Exporter Example
    type: http_json
    url: http://printer-status-exporter:8091/printers/printer_status_exporter_example/supplies
    low_threshold_percent: 20
    expected_status: 200
output:
  channel: discord
  target: alerts
  format: "anomalies only"
notifications:
  on_success: false
  on_failure: true
```

Policy validation requires `printer_id` values to exist and be enabled in:

```text
configs/printers/printers.yaml
```

The task URL must match the approved registry URL for that printer ID. The task
does not accept credentials in URLs, arbitrary printer endpoints, LAN discovery,
printer administration, or print-job actions.

Cross-check the internal exporter and approved printer registry with:

```bash
python scripts/configure_printer_status.py --dry-run \
  --printer-id office_laser \
  --name "Office Laser" \
  --upstream-url http://printer-adapter.local/supplies
python scripts/validate_printer_status.py
```

## Backup Verification Tasks

`backup_verification` tasks verify recent Yggy backups without shell access,
Docker socket access, database imports, or host filesystem mounts. The worker
reads only the project backup directory mounted at `/app/backups:ro` and rejects
backup roots outside that mount.

```yaml
type: backup_verification
backup:
  backup_root: /app/backups
  max_age_hours: 26
  min_mysql_dump_bytes: 1024
  secret_scan_enabled: true
  max_scan_bytes_per_file: 2000000
  required_files:
    - manifest.json
    - mysql/automation.sql
    - api/health.json
    - api/tasks.json
    - api/topics.json
    - api/openapi.json
    - git-commit.txt
output:
  channel: discord
  target: alerts
  format: "anomalies only"
notifications:
  on_success: false
  on_failure: true
```

The worker selects the newest `yggy-*` backup, checks age, parses
`manifest.json`, verifies required files, checks the MySQL dump size and header,
checks the manifest's no-secrets flags, and scans bounded file prefixes for
secret markers. It records file paths and match counts only; it does not record
matched secret values.

This is a restore dry-run validation, not an import. Manual restore still uses
`scripts/restore_yggy.sh --backup-dir <backup> --apply` after local operator
review.

## Run Safety Limits

Task policies can bound how often a task may be queued:

- `max_runs_per_hour`: maximum accepted queued/running/completed runs for this
  task in the trailing hour. Set to `null` to disable the hourly cap.
- `max_runs_per_day`: maximum accepted runs for this task in the trailing day.
  Set to `null` to disable the daily cap.
- `min_seconds_between_runs`: minimum time between accepted run queue attempts.
  Set to `0` to disable the minimum spacing rule.

The API enforces these limits before creating a run. A denied run attempt creates
an audit event with action `task.run.denied`; it does not create a run row and it
does not call the worker, Discord, n8n, or source fetchers.

## n8n Webhook Tasks

n8n is an execution backend, not the policy authority. Any task with an `n8n:`
block is validated by the automation API against the approved webhook registry.
The task config may reference only an approved webhook ID and internal webhook
path.

```yaml
type: n8n_webhook
output:
  channel: internal
  target: n8n
  format: "bounded webhook normalization status"
n8n:
  webhook_id: daily_briefing_stub
  path: /webhook/yggy-daily-briefing
  method: POST
  payload:
    purpose: daily_briefing_payload_normalizer
    delivery_target: briefings
    title: Daily Local AI Security Briefing
    summary: Example approved digest payload for n8n normalization.
    items:
      - title: Example local AI security item
        impact: Demonstrates bounded payload normalization without external posting.
        url: https://example.com/feed.xml
    sources:
      - https://example.com/feed.xml
```

For `topic_digest` tasks, the `n8n.payload` block should contain only stable
static metadata such as `purpose` and `delivery_target`. The worker builds the
dynamic digest fields from the approved digest result:

```yaml
type: topic_digest
output:
  channel: discord
  target: briefings
  format: "5 bullets, impact, source links, recommended action"
n8n:
  webhook_id: daily_briefing_stub
  path: /webhook/yggy-daily-briefing
  method: POST
  payload:
    purpose: daily_briefing_payload_normalizer
    delivery_target: briefings
```

The n8n response is recorded in the run log before Yggy applies its normal
notification policy. Discord delivery remains owned by Yggy.

The shared webhook secret is read from `N8N_WEBHOOK_SHARED_SECRET` by the
worker. n8n should validate the same header through its credential store, for
example with a Header Auth credential named `Yggy Webhook Header Auth`. Do not
place webhook secrets, URLs with credentials, n8n credential values, or API
tokens in task YAML.

Live n8n webhook responses are recorded in the run log as bounded, redacted JSON
when the workflow returns JSON. The starter workflow returns a normalized digest
payload summary and intentionally omits inbound request headers.

## Secret References

Reference credentials by stable names only, such as `discord_target: briefings`. Do not place raw tokens, webhooks, cookies, or passwords in YAML.
