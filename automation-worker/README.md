# Automation Worker

The worker polls the automation API for approved enabled tasks and runs bounded handlers. It does not execute arbitrary shell commands, use the Docker socket, or write arbitrary host files.

`DISCORD_DRY_RUN=true` keeps notification execution non-networked by default.

## Backup Verification

`backup_verification` tasks read only the configured `/app/backups:ro` mount.
The handler validates the newest `yggy-*` backup, required files, manifest
no-secret flags, MySQL dump header/size, and bounded secret-marker scan results.
It reports paths and counts only, never matched secret values. It does not run
restore scripts, Docker commands, MySQL clients, or arbitrary shell commands.

## Ollama Summarizer

Topic digests can use a local Ollama model when `LLM_SUMMARIZER_ENABLED=true`. The worker still fetches sources through bounded RSS/HTTP clients, treats all source text as untrusted data, and falls back to deterministic formatting when Ollama is unavailable, times out, or returns an empty response.

Recommended local default for this host is `granite4.1:8b`, selected from the installed model inventory because it produced complete impact/source bullets with lower latency than larger general models during a bounded local probe.

Topic digest source selection is controlled by the automation API policy. The
deployed policy requires `source_id` entries from `configs/sources/approved_sources.yaml`
and disables generic `web_query` sources for topic digests.

The worker also enforces the approved-source registry at execution time. It does
not fetch unapproved, disabled, or identity-mismatched sources even if an older
stored task config contains them. Digest run logs include `source_health`,
`approved_source_count`, and per-item source trust metadata so downstream n8n
payloads and Discord summaries can show where each item came from.

## Notification Preferences

Each task can declare `notifications` preferences. The worker classifies each
result as `success`, `empty`, or `failure`, applies the task toggles, suppresses
non-failure messages during quiet hours, collapses repeated failures inside the
configured window, and stores the final `notification_decision` in the run log.
