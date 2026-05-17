# Automation Worker

The worker polls the automation API for approved enabled tasks and runs bounded handlers. It does not execute arbitrary shell commands, use the Docker socket, or write arbitrary host files.

`DISCORD_DRY_RUN=true` keeps notification execution non-networked by default.

## Ollama Summarizer

Topic digests can use a local Ollama model when `LLM_SUMMARIZER_ENABLED=true`. The worker still fetches sources through bounded RSS/HTTP clients, treats all source text as untrusted data, and falls back to deterministic formatting when Ollama is unavailable, times out, or returns an empty response.

Recommended local default for this host is `granite4.1:8b`, selected from the installed model inventory because it produced complete impact/source bullets with lower latency than larger general models during a bounded local probe.

Topic digest source selection is controlled by the automation API policy. The
deployed policy requires `source_id` entries from `configs/sources/approved_sources.yaml`
and disables generic `web_query` sources for topic digests.

## Notification Preferences

Each task can declare `notifications` preferences. The worker classifies each
result as `success`, `empty`, or `failure`, applies the task toggles, suppresses
non-failure messages during quiet hours, collapses repeated failures inside the
configured window, and stores the final `notification_decision` in the run log.
