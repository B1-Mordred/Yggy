# Discord

Discord notifications are sent only to whitelisted targets from `configs/policies.yaml`.

Webhook URLs or Discord bot tokens belong in `.env`, Docker secrets, or a secret manager. They must not appear in task YAML, Open WebUI Knowledge, prompts, chat, Git, or logs.

Supported transports:

- Webhooks: `DISCORD_WEBHOOK_BRIEFINGS`, `DISCORD_WEBHOOK_ALERTS`, `DISCORD_WEBHOOK_APPROVALS`
- Bot token: `DISCORD_BOT_TOKEN` plus `DISCORD_CHANNEL_BRIEFINGS`, `DISCORD_CHANNEL_ALERTS`, `DISCORD_CHANNEL_APPROVALS`

If a target has both a webhook and bot channel configured, the webhook is used first. Bot-token sends disable Discord mentions by default through `allowed_mentions`.

`DISCORD_DRY_RUN=true` is the default and prevents network sends.
