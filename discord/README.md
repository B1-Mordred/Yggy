# Discord

Discord notifications are sent only to whitelisted targets from `configs/policies.yaml`.

Webhook URLs belong in `.env`, Docker secrets, or a secret manager. They must not appear in task YAML, Open WebUI Knowledge, prompts, chat, Git, or logs.

`DISCORD_DRY_RUN=true` is the default and prevents network sends.
