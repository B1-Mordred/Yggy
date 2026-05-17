# Configuration

Configuration here is declarative and non-secret. Task YAML may reference credential names, but must not include tokens, passwords, webhook URLs, private keys, cookies, or API keys.

Use `scripts/validate_configs.py` before enabling or deploying a task.

Bragi conversational memory lives in `configs/bragi/memory.yaml`. It is
mounted read-only and may contain only non-secret preferences, style notes,
service aliases, and operator preferences. It is not execution state and must
never contain credentials, webhook URLs, approval nonces, or admin decisions.

Persistent Bragi memory is stored in Bragi-owned database tables and is managed
through Bragi's `/memory/*` endpoints. It is user-scoped, explicit,
non-secret, inspectable, and forgettable. The identity registry in
`configs/identities.yaml` maps stable local user IDs to channel subject
references for Open WebUI, Discord, and future adapters. Use `_ref` fields for
deployment-specific identifiers and keep credentials out of this registry.

Reusable task templates live in `configs/task_templates/`. They render disabled,
dry-run task YAML through `scripts/render_task_template.py` and must still pass
the normal task schema and policy checks. See `docs/TASK_TEMPLATES.md`.

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

Backup verification tasks use an explicit `backup:` block and may read only the
worker's read-only `/app/backups` mount. They validate backup age, manifest
flags, required files, MySQL dump headers, and bounded secret-scan markers, then
alert only on anomalies when configured with `format: "anomalies only"`.
