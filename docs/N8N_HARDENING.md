# n8n Hardening

- Bind the UI to localhost unless proxied securely.
- Use TLS/reverse proxy if exposed.
- Enable 2FA or SSO if available.
- Set `N8N_ENCRYPTION_KEY`.
- Rotate the encryption key according to n8n guidance.
- Run n8n audit after configuration.
- Block Execute Command and Read/Write Files from Disk nodes where supported.
- Enable SSRF protection.
- Authenticate webhooks.
- Avoid public unauthenticated webhooks.
- Store credentials in n8n, not task YAML.

n8n is an execution backend. The automation API owns approval state.

## Yggy Webhook Dispatch

- Keep approved webhook IDs in `configs/n8n/webhooks.yaml`.
- Task YAML may reference only `webhook_id`, internal path, method, and bounded payload.
- Do not put webhook secrets in task YAML, prompts, Knowledge, docs, or logs.
- Set `N8N_WEBHOOK_SHARED_SECRET` in `.env` or another local secret store.
- The worker sends the secret as `X-Yggy-Webhook-Token`.
- Configure n8n workflows to verify that header before doing any work. Prefer
  n8n Webhook node Header Auth backed by an n8n credential named by reference,
  rather than storing a raw secret in workflow JSON.
- Keep n8n webhooks internal-only where possible; do not expose unauthenticated public webhooks.
- Automation API approval state remains authoritative even when n8n executes the workflow.
