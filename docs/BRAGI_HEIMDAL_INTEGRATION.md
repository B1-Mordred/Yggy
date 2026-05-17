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
  preferences later, answer ordinary chat through a local no-tool model
  fallback, and prepare canonical intents.
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

If a request maps to a registered capability but the Bragi service is not
authorized to call Yggdrasil, Bragi should say that the understood automation
request could not be forwarded because the service is not authorized. That is
an authorization failure, not a capability failure.

## Yggdrasil Boundary

Bragi forwards accepted requests to:

```text
POST /v1/yggdrasil/canonical-actions
```

That endpoint accepts only structured `draft_task_from_template` requests for
the milestone templates. It rejects raw natural language.

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
