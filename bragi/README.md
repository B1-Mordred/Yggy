# Bragi

Bragi is the natural human-facing agent for Yggy.

It is deliberately separate from Yggdrasil:

- Bragi talks naturally, asks clarifying questions, and prepares canonical intents.
- Heimdal, implemented inside the automation API, validates those intents against `configs/capabilities.yaml`.
- Yggdrasil receives only gateway-approved canonical actions.
- Yggy automation API remains the policy, approval, audit, and execution authority.

Bragi's voice is intentionally warmer than Yggdrasil's. He should sound like a
clear-spoken bard-scholar: practical, wry, culturally literate, lightly
sarcastic where it fits, and willing to use a little dark humor about broken
software and fate. That personality applies to conversation, not authority.
Approvals, execution, and task state still belong to Yggy.

Ordinary conversation is handled as normal chat through a local no-tool Ollama
fallback. That path cannot approve, configure, run, or forward automations. It
does not receive shell, Docker, filesystem, Discord, database, n8n, or admin
credentials.

Bragi exposes an OpenAI-compatible API:

```text
GET  /health
POST /diagnostics/route
POST /context/query
POST /memory/query
POST /memory/propose
POST /memory/commit
POST /memory/forget
GET  /intakes/pending-followups
POST /intakes/followups/mark-sent
POST /channels/discord/message
GET  /v1/models
POST /v1/chat/completions
```

`POST /diagnostics/route` is read-only. It classifies a request and reports
whether Bragi would keep it in chat, validate it through Heimdal, or forward a
structured canonical operation to Yggdrasil. The diagnostic path does not call
Ollama, Heimdal, Yggdrasil, Discord, or the automation API.

You can also ask Bragi directly:

```text
diagnose route: send daily brief now
```

`POST /context/query` is also read-only. It gives Bragi a redacted view of
safe context categories such as visible tasks, pending reviews by task status,
supported capabilities, approved source IDs, approved health-check IDs, approved
n8n webhook IDs, service status, recent run summaries, and non-secret memory.
It does not return approval nonces, admin-only approval records, raw run logs,
registry URLs, webhook URLs, tokens, passwords, or API keys.

Examples:

```text
what can you automate right now?
what is pending?
what sources can I use for a brief?
show sources for cybersecurity
find approved sources for German business news
what health checks do you know?
show recent run history
```

Natural source-search questions are answered from `GET /sources` only. Bragi
filters the approved registry, shows source IDs, ingestion mode, AI-safe fit,
region/language metadata when present, and flags metadata/link-only sources. It
does not fetch arbitrary URLs or forward source-search questions to Yggdrasil.
To change a digest, the user must still name approved source IDs or complete an
`awaiting_source_selection` intake, then confirm the resulting canonical intent.

Bragi also has controlled, user-scoped memory. Persistent memory is explicit,
non-secret, inspectable, and forgettable. Bragi does not silently store chat
history. A memory write starts as a pending proposal and is saved only after the
user replies `remember`.

Examples:

```text
Remember that I prefer short Discord alerts unless something failed.
remember
what do you remember about me?
forget Discord alerts
```

Memory is conversation context only. It is not approval state, task state,
credential state, or execution authority.

Bragi also exposes a narrow Discord ingress endpoint:

```text
POST /channels/discord/message
```

This endpoint is for a Discord bridge to call after it receives a message in a
registered channel. Bragi does not receive the bot token and does not post to
Discord itself. It validates the channel against `configs/channels.yaml`, checks
the configured environment references such as `DISCORD_HOME_CHANNEL` and
`DISCORD_ALLOWED_USER_IDS`, strips bot mentions, rejects attachments by default,
and returns a reply for the bridge to send.

Direct messages use a separate `discord_dm` channel entry. They are accepted only
when the bridge marks the payload as a DM and the author id appears in
`DISCORD_ALLOWED_USER_IDS`; arbitrary Discord channels are still rejected.

Discord is not an approval surface. Requests involving admin keys, tokens,
approval nonces, or approval/rejection decisions are refused with instructions
to use the local ops UI or admin CLI.

Configure Open WebUI as a separate model/provider for Bragi. Do not attach
Workspace Python tools, shell tools, Docker tools, filesystem write tools, admin
keys, approval nonces, webhook URLs, passwords, or tokens to Bragi.

Milestone-one capabilities:

- `server_health.v1`
- `topic_digest.v1`
- `topic_digest.modify_subjects.v1`
- `printer_supply_status.v1`
- `n8n_webhook.v1`

Bragi may ask for user confirmation before forwarding a request. That
confirmation only confirms understanding. It does not approve or enable the
automation; the Yggy approval path still applies.

Bragi can also collect details across a natural multi-turn conversation for a
new topic digest. For example, if the user gradually describes a daily morning
security briefing, then later provides sources such as Ubuntu security notices,
Ollama release notes, vulnerability announcements, patch notes, and NVD records,
Bragi first resolves those source-like descriptions against the approved source
registry and stores an `awaiting_source_selection` intake. The user can confirm
the default matches or choose numbered source options. Only after that does
Bragi show the canonical `topic_digest.v1` intent and an intake ID such as
`bragi_intake_20260518_001122_abcd1234`. Even phrases like `so be it` only close
the intake enough to display that canonical intent; they do not forward anything
to Yggdrasil until the user replies `confirm intake <id>` or `confirm` while the
intake is still visible in the conversation.

Bragi must not claim that it has contacted Yggdrasil, scheduled a briefing, or
that the user can expect future delivery unless a canonical Yggdrasil action
actually returned that result.

Intake commands:

```text
show pending intakes
show my pending requests
show all my pending requests
show pending Discord requests
show pending requests in this channel
show intake bragi_intake_...
continue request
continue Discord request
continue current request
continue intake bragi_intake_...
confirm intake bragi_intake_...
delete intake bragi_intake_...
cancel intake bragi_intake_...
confirm sources for intake bragi_intake_...
use sources 1 and 3 for intake bragi_intake_...
use docker_blog and send it to briefings for intake bragi_intake_...
```

Intakes are pre-execution state. They contain only non-secret canonical draft
intent material and expire by default after `BRAGI_INTAKE_TTL_SECONDS`.
Active intakes carry bounded follow-up metadata in their summary JSON. The
channel bridge may poll due follow-ups and mark a reminder as sent, but that
only updates reminder counters. It never confirms, approves, runs, or forwards
the intake.

Users can resume the same state manually before a timed reminder fires. If there
is one active intake, `continue request` shows the next safe action. If multiple
active intakes exist, Bragi asks the user to pick one by ID. This path is intake
management only; it does not contact Yggdrasil.

Intake visibility is user-scoped. Discord and Open WebUI both map through
`configs/channels.yaml` to a logical audience such as `local_user`; Bragi lists
or resumes only that user's intakes. Same-user intakes can be resumed across
configured channels, while another audience's intake ID is treated as not found.
Listings include the origin channel, created/updated timestamps, and the next
safe action needed.

For existing briefs, Bragi can propose bounded subject/source changes:

```text
add Docker security updates to the daily brief
add CISA and NVD to the security brief
remove n8n releases from the daily brief
include Open WebUI release notes in the daily local AI security briefing
```

These requests become canonical `propose_task_change` intents. They may only
use approved source IDs and filter terms, then Yggdrasil creates a pending Yggy
task-change proposal. Bragi does not approve, apply, enable, or run the change.

When the user names sources naturally instead of giving exact source IDs, Bragi
first reads `GET /sources`, shows the matching approved source IDs with their
ingestion modes, and stores a source-selection intake. The user can then reply
with `confirm sources for intake <id>` to use the default source matches, or
`use sources 1 and 3 for intake <id>` to choose numbered options. Only after
that source-selection step does Bragi generate the canonical
`topic_digest.modify_subjects.v1` intent for normal Heimdal validation and Yggy
confirmation. This prevents unsupported or ambiguous source names from being
forced into Yggdrasil.

When Heimdal says a canonical intent is missing details, Bragi also stores that
state as an intake instead of relying on fragile chat transcript parsing. The
user can provide the missing slots later by including the intake ID, for example:

```text
use docker_blog and send it to briefings for intake bragi_intake_...
```

Bragi revalidates the updated canonical intent through Heimdal before anything
can reach Yggdrasil. The incomplete-intake reply must always show what is
missing and offer both paths: complete the request with more details, or delete
the incomplete request with `delete intake <id>`. If the incomplete request is
still visible in the current conversation, `delete it` is also accepted.

Useful runtime settings:

```text
BRAGI_GENERAL_CHAT_ENABLED=true
BRAGI_CHAT_MODEL=llama3.1:8b
BRAGI_CHAT_TEMPERATURE=0.55
BRAGI_CHAT_TIMEOUT=30
BRAGI_CHAT_NUM_CTX=4096
BRAGI_CHAT_MAX_TOKENS=512
BRAGI_DEFAULT_USER_ID=local_user
BRAGI_CONFIG_ROOT=/app/configs
BRAGI_MEMORY_FILE=/app/configs/bragi/memory.yaml
BRAGI_MEMORY_DATABASE_URL=mysql+pymysql://automation:...@automation-mysql:3306/automation
BRAGI_INTAKE_TTL_SECONDS=86400
OLLAMA_BASE_URL=http://host.docker.internal:11434
DISCORD_HOME_CHANNEL=...
DISCORD_ALLOWED_USER_IDS=...
```

`BRAGI_MEMORY_FILE` is read-only non-secret context. It can hold preferences,
service aliases, and style notes, but never credentials, approval nonces, or
execution state.
