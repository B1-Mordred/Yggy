# Configuration

Configuration here is declarative and non-secret. Task YAML may reference credential names, but must not include tokens, passwords, webhook URLs, private keys, cookies, or API keys.

Use `scripts/validate_configs.py` before enabling or deploying a task.

Topic digest tasks must use source IDs from `configs/sources/approved_sources.yaml`.
Generic `web_query` sources are disabled by policy for topic digests so the worker
uses explicit approved feeds rather than broad search-style prompts.
Each approved source carries a trust level, categories, enabled flag, and
optional per-source item cap. The worker records that metadata in each digest run
and refuses to fetch disabled or unapproved sources at execution time.

Any task with an `n8n:` block must use webhook IDs from `configs/n8n/webhooks.yaml`.
Task YAML may include the approved path and bounded static payload metadata, but
never webhook secrets or n8n credentials. For topic digests, the worker builds
dynamic digest payload fields at runtime from the approved digest result.

Service health metrics are configured in `configs/metrics/services.yaml`. This
file is a static allowlist for the internal metrics exporter. Add only local
HTTP health or inventory endpoints that are safe to read and do not require
secrets.
