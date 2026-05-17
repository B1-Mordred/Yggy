# Architecture

Yggy turns Open WebUI, Hermes/yggdrasil, and Bragi into bounded local
automation interfaces without making any model the execution authority.

## Components

- Open WebUI: user interface.
- Bragi: natural human-facing agent for supported automation requests.
- Heimdal capability gateway: automation-api module that validates canonical
  intents against `configs/capabilities.yaml`.
- Hermes/yggdrasil: deterministic task-drafting and canonical action layer.
- automation-api: policy authority and OpenAPI tool server.
- MySQL: durable storage for tasks, approvals, run logs, and audit events.
- automation-worker: bounded executor for approved tasks.
- metrics-exporter: internal-only read-only HTTP health inventory adapter.
- n8n: optional workflow backend. It is never approval authority.
- Discord bridge: notification output through whitelisted targets.
- Ollama: optional summarizer adapter, disabled by default.

## Control Flow

```text
Natural path:
User -> Open WebUI -> Bragi -> Heimdal capability gateway
Heimdal -> yggdrasil canonical action endpoint -> automation-api

Deterministic path:
User -> Open WebUI -> yggdrasil -> automation-api

automation-api -> validate schema and policy
automation-api -> create disabled draft and approval request
local admin CLI/UI -> approve with nonce
automation-worker -> execute approved bounded handler
automation-worker -> read bounded metrics-exporter health data when configured
automation-worker -> record run and optionally notify Discord
```

## Trust Boundaries

The automation API is the policy boundary. Workers and n8n execute only bounded task types and only after the API says a task is enabled and approved.

Bragi is a concierge, not an authority. It may talk naturally and prepare a
canonical intent, but it cannot approve, enable, or run high-risk work. Heimdal
rejects unsupported capabilities, unsafe slots, broad web queries for topic
digests, arbitrary n8n webhook URLs, and unregistered service/source/webhook
IDs before anything reaches Yggdrasil.

Yggdrasil remains deterministic. Its canonical action endpoint accepts only
structured `draft_task_from_template` requests for registered milestone
templates and rejects raw natural language.

Open WebUI Tools/Functions are not used for broad Python execution. Open WebUI should ingest only the automation API OpenAPI spec and should receive only the low-privilege tool key.

The metrics exporter is intentionally not a host-management agent. It performs only allowlisted HTTP GET checks from `configs/metrics/services.yaml`; it has no Docker socket, shell, host networking, or broad host filesystem access.

## MySQL

The Docker scaffold uses an internal MySQL service named `automation-mysql`. The API connects through `DATABASE_URL` and reports database health generically through `/health`.
