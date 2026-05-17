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
GET  /v1/models
POST /v1/chat/completions
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
BRAGI_CHAT_MODEL=qwen3.5:9b
BRAGI_CHAT_TEMPERATURE=0.55
OLLAMA_BASE_URL=http://host.docker.internal:11434
```
