# n8n

n8n is an optional workflow execution backend. It is not the approval authority.

The automation API owns task approval state. The worker may call specific authenticated or internal-only n8n webhooks for approved tasks.

The compose scaffold binds the n8n UI to `127.0.0.1` and blocks risky nodes where supported.
