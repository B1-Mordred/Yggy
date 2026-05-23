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
CHANNEL_BRIDGE_FOLLOWUPS_ENABLED
CHANNEL_BRIDGE_FOLLOWUP_POLL_SECONDS
CHANNEL_BRIDGE_FOLLOWUP_LIMIT
CHANNEL_BRIDGE_NOTIFICATIONS_ENABLED
CHANNEL_BRIDGE_NOTIFICATION_LIMIT
```

Rules:

- ignore bot messages
- only process configured Discord channels or their threads
- only process direct messages when an explicit `discord_dm` channel is enabled
  and the author is listed in `DISCORD_ALLOWED_USER_IDS`
- optionally enforce `DISCORD_ALLOWED_USER_IDS`
- pass bounded recent channel history to Bragi for confirmations
- poll Bragi for due intake follow-ups and post bounded reminders to the configured channel
- poll the Yggy automation API for pending channel notifications, including
  capability-implementation status changes, and post them through the same
  configured Discord surface
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

## Intake Follow-Ups

When enabled, the bridge polls:

```text
GET /intakes/pending-followups?channel=discord
```

for configured audiences and posts Bragi's reminder text to the matching
Discord channel. After a successful post it calls:

```text
POST /intakes/followups/mark-sent
```

This only advances reminder metadata. It does not confirm, approve, run, or
forward anything to Yggdrasil.

## Channel Notifications

The automation API owns a generic channel-notification outbox for status changes
that happen after the original request has returned. The first producer is the
capability-implementation workflow. When an implementation run moves through
`queued`, `running`, `completed`, or `failed`, the API stores a redacted Bragi
persona message for the requesting audience and source channel.

When enabled, the bridge polls:

```text
GET /channels/notifications/pending?channel=discord&user_id=<audience>
GET /channels/notifications/pending?channel=discord_dm&user_id=<audience>
```

After a successful post it calls:

```text
POST /channels/notifications/{notification_id}/mark
```

These calls use `AUTOMATION_CHANNEL_BRIDGE_API_KEY`. They can read and mark
delivery state only; they cannot approve, run, mutate, deploy, or call
Yggdrasil. Discord delivery uses the configured channel id. Discord DM delivery
uses the explicitly allowed user ids from `DISCORD_ALLOWED_USER_IDS`.

Open WebUI-originated notifications are also stored in the outbox with
`channel=openwebui`, but this repository does not yet include an outbound Open
WebUI push adapter. Until such an adapter exists, those messages are durable
and admin-visible but not pushed into an Open WebUI chat session.

## Direct Messages

Discord DMs are not accepted through the normal channel allowlist because each
DM has a Discord-generated channel id. To enable private messages, add a
separate `discord_dm` entry in `configs/channels.yaml` and point
`allowed_user_ids_ref` at `DISCORD_ALLOWED_USER_IDS`.

DMs are still a model-facing channel. They may chat, read safe context, draft
tasks, and run approved L0/L1 actions according to the configured capabilities.
They cannot approve tasks, expose secrets, or bypass Bragi, Heimdal, Yggdrasil,
or Yggy policy.
