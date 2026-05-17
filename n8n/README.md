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

## Stub Workflow

`workflows/daily_briefing_webhook_stub.json` is a minimal importable starting
point. After importing it, configure the Webhook node path to:

```text
yggy-daily-briefing
```

Then add an IF or Code node that checks header `x-yggy-webhook-token` against an
n8n credential or environment-backed value. Avoid Execute Command and filesystem
nodes. Keep any n8n credentials in n8n's credential store.
