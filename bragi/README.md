# Bragi

Bragi is the natural human-facing agent for Yggy.

It is deliberately separate from Yggdrasil:

- Bragi talks naturally, asks clarifying questions, and prepares canonical intents.
- Heimdal, implemented inside the automation API, validates those intents against `configs/capabilities.yaml`.
- Yggdrasil receives only gateway-approved canonical actions.
- Yggy automation API remains the policy, approval, audit, and execution authority.

Ordinary conversation is handled as normal chat through a local no-tool Ollama
fallback. That path cannot approve, configure, run, or forward automations. It
does not receive shell, Docker, filesystem, Discord, database, n8n, or admin
credentials.

Bragi exposes an OpenAI-compatible API:

```text
GET  /health
POST /diagnostics/route
POST /context/query
POST /memory/query
POST /memory/propose
POST /memory/commit
POST /memory/forget
POST /channels/discord/message
GET  /v1/models
POST /v1/chat/completions
```

`POST /diagnostics/route` is read-only. It classifies a request and reports
whether Bragi would keep it in chat, validate it through Heimdal, or forward a
structured canonical operation to Yggdrasil. The diagnostic path does not call
Ollama, Heimdal, Yggdrasil, Discord, or the automation API.

You can also ask Bragi directly:

```text
diagnose route: send daily brief now
```

`POST /context/query` is also read-only. It gives Bragi a redacted view of
safe context categories such as visible tasks, pending reviews by task status,
supported capabilities, approved source IDs, approved health-check IDs, approved
n8n webhook IDs, service status, recent run summaries, and non-secret memory.
It does not return approval nonces, admin-only approval records, raw run logs,
registry URLs, webhook URLs, tokens, passwords, or API keys.

Examples:

```text
what can you automate right now?
what is pending?
what sources can I use for a brief?
what health checks do you know?
show recent run history
```

Bragi also has controlled, user-scoped memory. Persistent memory is explicit,
non-secret, inspectable, and forgettable. Bragi does not silently store chat
history. A memory write starts as a pending proposal and is saved only after the
user replies `remember`.

Examples:

```text
Remember that I prefer short Discord alerts unless something failed.
remember
what do you remember about me?
forget Discord alerts
```

Memory is conversation context only. It is not approval state, task state,
credential state, or execution authority.

Bragi also exposes a narrow Discord ingress endpoint:

```text
POST /channels/discord/message
```

This endpoint is for a Discord bridge to call after it receives a message in a
registered channel. Bragi does not receive the bot token and does not post to
Discord itself. It validates the channel against `configs/channels.yaml`, checks
the configured environment references such as `DISCORD_HOME_CHANNEL` and
`DISCORD_ALLOWED_USER_IDS`, strips bot mentions, rejects attachments by default,
and returns a reply for the bridge to send.

Discord is not an approval surface. Requests involving admin keys, tokens,
approval nonces, or approval/rejection decisions are refused with instructions
to use the local ops UI or admin CLI.

Configure Open WebUI as a separate model/provider for Bragi. Do not attach
Workspace Python tools, shell tools, Docker tools, filesystem write tools, admin
keys, approval nonces, webhook URLs, passwords, or tokens to Bragi.

Milestone-one capabilities:

- `server_health.v1`
- `topic_digest.v1`
- `n8n_webhook.v1`

Bragi may ask for user confirmation before forwarding a request. That
confirmation only confirms understanding. It does not approve or enable the
automation; the Yggy approval path still applies.

Useful runtime settings:

```text
BRAGI_GENERAL_CHAT_ENABLED=true
BRAGI_CHAT_MODEL=llama3.1:8b
BRAGI_CHAT_TEMPERATURE=0.55
BRAGI_CHAT_TIMEOUT=30
BRAGI_CHAT_NUM_CTX=4096
BRAGI_CHAT_MAX_TOKENS=512
BRAGI_DEFAULT_USER_ID=local_user
BRAGI_CONFIG_ROOT=/app/configs
BRAGI_MEMORY_FILE=/app/configs/bragi/memory.yaml
BRAGI_MEMORY_DATABASE_URL=mysql+pymysql://automation:...@automation-mysql:3306/automation
OLLAMA_BASE_URL=http://host.docker.internal:11434
DISCORD_HOME_CHANNEL=...
DISCORD_ALLOWED_USER_IDS=...
```

`BRAGI_MEMORY_FILE` is read-only non-secret context. It can hold preferences,
service aliases, and style notes, but never credentials, approval nonces, or
execution state.
