# Architecture

Yggy turns Hermes/yggdrasil into a coordinator for bounded local automations.

## Components

- Open WebUI: user interface.
- Hermes/yggdrasil: reasoning and task-drafting layer.
- automation-api: policy authority and OpenAPI tool server.
- MySQL: durable storage for tasks, approvals, run logs, and audit events.
- automation-worker: bounded executor for approved tasks.
- n8n: optional workflow backend. It is never approval authority.
- Discord bridge: notification output through whitelisted targets.
- Ollama: optional summarizer adapter, disabled by default.

## Control Flow

```text
User -> Open WebUI -> yggdrasil -> automation-api
automation-api -> validate schema and policy
automation-api -> create disabled draft and approval request
local admin CLI/UI -> approve with nonce
automation-worker -> execute approved bounded handler
automation-worker -> record run and optionally notify Discord
```

## Trust Boundaries

The automation API is the policy boundary. Workers and n8n execute only bounded task types and only after the API says a task is enabled and approved.

Open WebUI Tools/Functions are not used for broad Python execution. Open WebUI should ingest only the automation API OpenAPI spec and should receive only the low-privilege tool key.

## MySQL

The Docker scaffold uses an internal MySQL service named `automation-mysql`. The API connects through `DATABASE_URL` and reports database health generically through `/health`.
