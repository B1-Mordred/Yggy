# n8n

n8n is an optional workflow execution backend. It is not the approval authority.

The automation API owns task approval state. The worker may call specific authenticated or internal-only n8n webhooks for approved tasks.

The compose scaffold binds the n8n UI to `127.0.0.1` and blocks risky nodes where supported.

## Approved Webhooks

Approved webhook IDs live in:

```text
configs/n8n/webhooks.yaml
```

Task YAML references a `webhook_id` and path from that registry. The worker builds
the URL from `N8N_WEBHOOK_BASE_URL`, defaulting to `http://n8n:5678`, and sends
the shared secret from `N8N_WEBHOOK_SHARED_SECRET` in the
`X-Yggy-Webhook-Token` header. The secret must stay in `.env` or another local
secret store, not task YAML or Open WebUI Knowledge.

The starter task is:

```text
configs/tasks/example_n8n_webhook.yaml
```

It is disabled and dry-run by default. Dry-run mode records the dispatch payload
shape but does not call n8n.

## Payload Normalizer Workflow

`workflows/daily_briefing_webhook_stub.json` is a minimal importable starting
point. It uses n8n's built-in Header Auth on the Webhook node, so requests with
missing or wrong `X-Yggy-Webhook-Token` values are rejected before the workflow
body runs. After authentication, it normalizes the approved Yggy dispatch payload
and returns a bounded JSON result to the worker. It does not send Discord
messages, call external APIs, write files, or execute commands.

Before activating the workflow, create an n8n credential named:

```text
Yggy Webhook Header Auth
```

Use credential type `Header Auth` with:

```text
Name: X-Yggy-Webhook-Token
Value: value from N8N_WEBHOOK_SHARED_SECRET
```

Store that credential in n8n's credential store only. Do not commit the value or
place it in task YAML, prompts, Open WebUI Knowledge, or Markdown docs.

The imported Webhook node path is:

```text
yggy-daily-briefing
```

The response includes fields such as `action`, `task_id`, `run_id`,
`payload_keys`, `delivery_target`, `purpose`, and a bounded `normalized` object.
The workflow intentionally does not return inbound request headers.

Avoid Execute Command and filesystem nodes. Keep any n8n credentials in n8n's
credential store.
