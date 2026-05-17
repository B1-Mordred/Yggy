# Channel Bridge

The channel bridge is the first-class human-channel ingress for Bragi.

Milestone one supports Discord:

```text
Discord
  -> channel-bridge
      -> Bragi /channels/discord/message
          -> Heimdal/Yggdrasil/Yggy only when Bragi routes a known capability
```

The bridge owns Discord transport credentials. Bragi does not receive the
Discord bot token or webhook URLs.

Runtime inputs:

```text
DISCORD_BOT_TOKEN
DISCORD_HOME_CHANNEL
DISCORD_ALLOWED_USER_IDS
CHANNEL_BRIDGE_BRAGI_BASE_URL
CHANNEL_BRIDGE_BRAGI_API_KEY
CHANNEL_BRIDGE_AUTOMATION_API_BASE_URL
CHANNEL_BRIDGE_AUTOMATION_API_KEY
CHANNEL_BRIDGE_CONFIG_ROOT
```

Rules:

- ignore bot messages
- only process configured Discord channels or their threads
- optionally enforce `DISCORD_ALLOWED_USER_IDS`
- pass bounded recent channel history to Bragi for confirmations
- do not approve tasks from Discord
- do not expose admin keys, approval nonces, webhook URLs, or database secrets
- post Bragi replies with Discord mentions disabled
- record redacted channel audit events through `POST /channels/events`

The bridge is a transport adapter only. Policy and execution authority remain in
Bragi, Heimdal, Yggdrasil, and the Yggy automation API.

## Audit Events

The bridge can use a dedicated `AUTOMATION_CHANNEL_BRIDGE_API_KEY`. This key is
not an admin, worker, or model tool key. It can write channel ingress audit
events and cannot approve tasks.

Channel audit events store:

- channel config id
- hashed channel and author ids
- Discord message id
- route, required capability, and whether Yggdrasil was reached
- redacted 240-character request/reply previews
- bounded metadata such as attachment and history counts

They must not store raw Discord archives, bot tokens, webhook URLs, approval
nonces, API keys, passwords, or attachment contents.
