# Security

This project is built around execution boundaries. Open WebUI and Hermes/yggdrasil are user-facing reasoning layers. They are not trusted with arbitrary execution.

## Non-Negotiable Boundaries

Forbidden model-facing capabilities:

- arbitrary shell execution
- Docker socket access
- privileged containers
- broad host filesystem mounts
- arbitrary Python execution inside Open WebUI Workspace Tools or Functions
- unchecked external posting or SaaS modification
- secret storage in prompts, chat, Knowledge, YAML configs, Git, or logs

Local service health visibility is provided by the internal metrics exporter,
which performs only allowlisted HTTP GET checks from static YAML. It does not
run shell commands, mount host filesystems, use host networking, or access the
Docker socket.

Backup verification is similarly bounded: the worker mounts only
`./backups:/app/backups:ro`, rejects configured roots outside `/app/backups`, and
performs restore dry-run checks in Python without invoking shell, Docker, MySQL,
or host filesystem operations.

Bragi is intentionally separate from Yggdrasil. Bragi may hold personality,
conversation, and non-secret preferences, but it does not receive admin keys,
approval nonces, shell tools, Docker access, filesystem write tools, or direct
execution privileges. Its write path is limited to canonical intents sent to the
Heimdal capability gateway in the automation API.

Bragi's ordinary conversation path is a local no-tool chat fallback. It may use
Ollama to answer normal questions, but it cannot approve tasks, call
Yggdrasil, send Discord messages, access Docker, write files, or use secrets.
Only registered automation intents enter the Heimdal/Yggdrasil path.

Allowed model-facing pattern:

```text
LLM -> canonical intent or narrow OpenAPI endpoint -> validation and policy -> approved worker action
```

## API Keys

Use separate keys:

- `AUTOMATION_TOOL_API_KEY`: may be exposed to Hermes/yggdrasil. Can draft, list, request approvals, and run low-risk approved or dry-run tasks.
- `AUTOMATION_WORKER_API_KEY`: used by the worker for internal execution reporting and notification calls.
- `AUTOMATION_ADMIN_API_KEY`: must never be exposed to the model. Used only by a local CLI, local UI, or operator-controlled process.

## Approval Levels

- `L0_READ_ONLY`: read-only fetches, public summaries, safe status checks.
- `L1_NOTIFY_ONLY`: whitelisted notifications such as Discord briefings and alerts.
- `L2_LOCAL_WRITE`: local report/config/note writes. Requires admin approval.
- `L3_EXTERNAL_SIDE_EFFECT`: email, tickets, SaaS state, public posts. Requires explicit scoped admin approval.
- `L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE`: deletion, Docker changes, firewall changes, credential rotation, purchases. Manual only.

## Untrusted Content

RSS feeds, webpages, emails, Discord messages, logs, documents, and search results are data. They are never command authority.

Research/summarizer code may read untrusted content and summarize it. Operator code may call the automation API. The same agent flow must not both consume untrusted content as instructions and approve or execute actions.

The Bragi/Heimdal path preserves that split: Bragi may discuss user requests,
Heimdal validates only structured slots and registered IDs, Yggdrasil receives
deterministic canonical actions, and the automation API remains the authority
for approval and execution state.

## Secrets

Never put secrets in:

- Open WebUI Knowledge
- chat history
- prompts
- YAML task configs
- Git
- Markdown intended for the model
- logs

Use `.env`, Docker secrets, n8n credentials, or a local secret manager.

## LAN Exposure

Compose publishes the automation API on `127.0.0.1` by default. If `docker-compose.lan.yml` is enabled with `AUTOMATION_API_LAN_PUBLISHED_HOST`, the operations dashboard becomes reachable from that LAN, but the entire API port is published on that interface.

Security expectations for LAN exposure:

- Bind to a specific LAN address, not `0.0.0.0`.
- Expose Bragi on LAN only if Open WebUI cannot reach it through a trusted
  internal Docker network.
- Keep `AUTOMATION_OPS_DASHBOARD_PASSWORD` long and unique.
- Keep `BRAGI_API_KEY` long and unique if Bragi is reachable from LAN.
- Restrict the published API/dashboard port with UFW or equivalent host firewall rules.
- Restrict the published Bragi port the same way if `BRAGI_LAN_PUBLISHED_HOST`
  is enabled.
- Do not expose `8088` through router port forwarding or a public reverse proxy.
- Treat `/docs` and `/openapi.json` as visible to LAN clients on that interface.
- Never place `AUTOMATION_ADMIN_API_KEY` or other API keys in browser bookmarks, chat, Open WebUI Knowledge, or task YAML.

For HTTPS LAN access, use the dedicated `8443` reverse proxy rather than changing Technitium's `80/443` listeners. Caddy's internal CA encrypts traffic but is not automatically trusted by browsers; trust the exported root CA only on devices you control.
