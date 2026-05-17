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
BRAGI_MEMORY_FILE=/app/configs/bragi/memory.yaml
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

`BRAGI_MEMORY_FILE` is read-only non-secret context. It can hold preferences,
service aliases, and style notes, but never credentials, approval nonces, or
execution state.
