# Task Schema

Task YAML is declarative and non-secret.

```yaml
id: daily_local_ai_security_briefing
name: Daily Local AI Security Briefing
type: topic_digest
enabled: false
owner: local_user
created_by: yggdrasil

trigger:
  kind: schedule
  cron: "0 8 * * 1-5"
  timezone: Europe/Berlin

sources:
  - type: rss
    url: https://example.com/feed.xml
  - type: web_query
    query: "Open WebUI Ollama Hermes Docker security"

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
  dry_run: true
  timeout_seconds: 120
  retry_count: 1
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

## Secret References

Reference credentials by stable names only, such as `discord_target: briefings`. Do not place raw tokens, webhooks, cookies, or passwords in YAML.
