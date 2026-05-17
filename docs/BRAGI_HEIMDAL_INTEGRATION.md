# Bragi and Heimdal Integration

Bragi adds a natural human-facing layer without making Yggdrasil free-form
again.

```text
Human
  -> Bragi natural agent
      -> Heimdal capability gateway in automation-api
          -> Yggdrasil canonical action endpoint
              -> Yggy automation-api policy and approval path
```

## Roles

- **Bragi** is conversational. It may clarify, explain, remember non-secret
  preferences from a read-only memory file, answer ordinary chat through a local
  no-tool model fallback, and prepare canonical intents.
- **Heimdal** is the strict gateway. It validates canonical intents against
  `configs/capabilities.yaml`.
- **Yggdrasil** remains deterministic. It receives only canonical actions from
  Heimdal-approved requests.
- **Yggy automation-api** remains authoritative for validation, drafts,
  approvals, runs, audit logs, approved sources, approved n8n webhooks, and
  Discord target policy.

## Capability Registry

The first registry lives at:

```text
configs/capabilities.yaml
```

It is explicit, versioned, and inspected by the automation API. Milestone one
allows only:

- `server_health.v1`
- `topic_digest.v1`
- `n8n_webhook.v1`

Each capability maps to an existing task template. Unknown capabilities,
unsafe requests, unapproved source IDs, unapproved health checks, unapproved n8n
webhook IDs, and broad `web_query` style requests are rejected before reaching
Yggdrasil.

## Canonical Intent

Bragi sends canonical intents to:

```text
POST /capabilities/validate-intent
POST /capabilities/prepare-yggdrasil-request
```

The gateway returns one of:

```text
ACCEPT
ASK_CLARIFICATION
REJECT_UNSUPPORTED
REJECT_UNSAFE
PROPOSE_NEW_CAPABILITY
```

Only `ACCEPT` responses include a deterministic `yggdrasil_request`.

If a user message is ordinary conversation and does not look like an automation
request, Bragi should answer it as normal chat. It should not tell the user that
the request cannot be sent to Yggdrasil merely because there is no matching
capability.

Bragi uses an explicit request-mode split:

- Help/meta questions stay conversational.
- Discussion requests stay conversational, even if they mention briefs,
  summaries, Docker, or local AI.
- Direct draft requests become canonical `draft_task` intents and are validated
  by Heimdal.
- Direct list/show/run/pause requests become structured Yggdrasil canonical
  operations such as `list_tasks`, `show_task`, `run_task`, and `pause_task`.

If a draft request is missing required slots, Bragi returns a partial canonical
intent and asks for the missing details. Follow-up replies are merged into that
same intent and revalidated before anything reaches Yggdrasil.

If a request maps to a registered capability but the Bragi service is not
authorized to call Yggdrasil, Bragi should say that the understood automation
request could not be forwarded because the service is not authorized. That is
an authorization failure, not a capability failure.

## Route Diagnostics

Bragi exposes a read-only route diagnostic endpoint:

```text
POST /diagnostics/route
```

The endpoint accepts either:

```json
{"text": "send daily brief now"}
```

or:

```json
{"messages": [{"role": "user", "content": "send daily brief now"}]}
```

It returns the request mode, proposed internal route, and any candidate
canonical operation or intent. It does not call Ollama, Heimdal, Yggdrasil,
Discord, or the automation API, and it removes the raw `user_request` field from
candidate intents.

For quick troubleshooting from Open WebUI, ask Bragi:

```text
diagnose route: how can i add a new subject to the brief?
diagnose route: send daily brief now
diagnose route: draft a weekday 08:00 local AI security briefing
```

This is meant to make routing decisions visible without weakening the execution
boundary. Diagnostics are not approval, execution, or a source of authority.

## Yggdrasil Boundary

Bragi forwards accepted requests to:

```text
POST /v1/yggdrasil/canonical-actions
```

That endpoint accepts only structured `draft_task_from_template` requests for
the milestone templates and structured task operations. It rejects raw natural
language.

Supported canonical operations:

```text
draft_task_from_template
list_tasks
show_task
run_task
pause_task
```

Run and pause operations still go through the automation API, so task approval,
dry-run state, rate limits, active-run locks, and role checks remain
authoritative there.

## Non-Secret Memory

Bragi can read:

```text
configs/bragi/memory.yaml
```

This file is mounted read-only into the Bragi container. It is conversation
context only, not execution state and not approval authority.

Allowed examples:

- preferred language
- message style
- default timezone
- non-secret service aliases
- automation preferences

Forbidden examples:

- API keys
- tokens
- passwords
- webhook URLs
- approval nonces
- cookies
- private keys

If the memory file contains secret-like keys or values, Bragi ignores it.

## Open WebUI

Use Bragi as a separate OpenAI-compatible model/provider. Keep the existing
Yggdrasil model strict and deterministic.

Do not attach these to Bragi:

- Workspace Python tools
- shell or terminal tools
- Docker socket access
- filesystem write tools
- admin API key
- approval nonces
- webhook URLs, tokens, passwords, cookies, or private keys

Bragi needs only the model-facing automation tool key and, if configured, the
Yggdrasil action API key.
