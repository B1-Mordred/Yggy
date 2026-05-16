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
