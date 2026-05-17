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

## n8n Webhook Tasks

n8n is an execution backend, not the policy authority. `n8n_webhook` tasks are
approved by the automation API like any other task. The task config may reference
only an approved webhook ID and internal webhook path:

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
