# Bragi and Heimdal Integration

Bragi adds a natural human-facing layer without making Yggdrasil free-form
again.

```text
Human
  -> Bragi natural agent
      -> Heimdal capability gateway in automation-api
          -> Yggdrasil canonical action endpoint
              -> Yggy automation-api policy and approval path
```

## Roles

- **Bragi** is conversational. It may clarify, explain, remember non-secret
  preferences from a read-only memory file, answer ordinary chat through a local
  no-tool model fallback, and prepare canonical intents.
- **Heimdal** is the strict gateway. It validates canonical intents against
  `configs/capabilities.yaml`.
- **Yggdrasil** remains deterministic. It receives only canonical actions from
  Heimdal-approved requests.
- **Yggy automation-api** remains authoritative for validation, drafts,
  approvals, runs, audit logs, approved sources, approved n8n webhooks, and
  Discord target policy.

Bragi's personality belongs to the conversation layer. He should sound like a
warm, wry bard-scholar: direct, practical, occasionally lyrical, and lightly
sarcastic where the situation deserves it. This is intentionally different from
Yggdrasil's strict process-compatible voice. The personality must never become
authority: Bragi may joke about entropy and broken software, but approvals,
runs, task state, secrets, and execution remain behind Heimdal/Yggy.

## Capability Registry

The first registry lives at:

```text
configs/capabilities.yaml
```

It is explicit, versioned, and inspected by the automation API. Milestone one
allows only:

- `server_health.v1`
- `topic_digest.v1`
- `topic_digest.modify_subjects.v1`
- `printer_supply_status.v1`
- `n8n_webhook.v1`

Draft capabilities map to existing task templates. The topic-digest subject
change capability maps to an existing task-change proposal flow. Unknown
capabilities, unsafe requests, unapproved source IDs, unapproved health checks,
unapproved printer IDs, unapproved n8n webhook IDs, and broad `web_query` style
requests are rejected before reaching Yggdrasil.

## Canonical Intent

Bragi sends canonical intents to:

```text
POST /capabilities/validate-intent
POST /capabilities/prepare-yggdrasil-request
```

The gateway returns one of:

```text
ACCEPT
ASK_CLARIFICATION
REJECT_UNSUPPORTED
REJECT_UNSAFE
PROPOSE_NEW_CAPABILITY
```

Only `ACCEPT` responses include a deterministic `yggdrasil_request`.

If a user message is ordinary conversation and does not look like an automation
request, Bragi should answer it as normal chat. It should not tell the user that
the request cannot be sent to Yggdrasil merely because there is no matching
capability.

Bragi uses an explicit request-mode split:

- Help/meta questions stay conversational.
- Discussion requests stay conversational, even if they mention briefs,
  summaries, Docker, or local AI.
- Direct draft requests become canonical `draft_task` intents and are validated
  by Heimdal.
- Direct requests to add, remove, include, or stop covering subjects in an
  existing digest become canonical `propose_task_change` intents and are
  validated by Heimdal.
- Direct list/show/run/pause requests become structured Yggdrasil canonical
  operations such as `list_tasks`, `show_task`, `run_task`, and `pause_task`.

## Goal Intake Router

Bragi has a deterministic goal intake router before executable routing. It is
enabled by default with:

```text
BRAGI_GOAL_ROUTER_ENABLED=true
BRAGI_GOAL_ROUTER_REQUIRE_CONFIRMATION=true
BRAGI_GOAL_ROUTER_MAX_CANDIDATES=5
```

The default router is deterministic and has no Hermes or LLM dependency. The
implementation lives in:

```text
bragi/bragi/goal_models.py
bragi/bragi/goal_loop.py
bragi/bragi/hermes_client.py
bragi/bragi/goal_prompts.py
```

`bragi/bragi/goal_router.py` remains as a compatibility import layer for older
tests and callers. The goal loop classifies each automation-shaped request into
one of these internal request kinds:

```text
list_existing
inspect_existing
run_existing
pause_existing
modify_existing
create_new
propose_new_capability
unsafe
needs_clarification
chat
```

The router also records whether the target is an existing task, a new task, a
new capability proposal, or still unknown. Existing task references are resolved
before creating new tasks. Explicit slug-like task IDs win; configured
`TASK_ALIASES` are allowed; and visible task summaries from the automation API
can be used read-only when an ambiguous target must be resolved. If more than
one task matches, Bragi asks the user to choose, stores a `collecting_slots`
intake, and does not forward anything.

The routing outcomes map to the existing safe paths:

```text
list_existing      -> Yggdrasil canonical action: list_tasks
inspect_existing   -> Yggdrasil canonical action: show_task
run_existing       -> Yggdrasil canonical action: run_task
pause_existing     -> Yggdrasil canonical action: pause_task
modify_existing    -> CanonicalIntent: propose_task_change
create_new         -> CanonicalIntent: draft_task
new capability     -> non-executable capability proposal
unsafe             -> safe rejection or monitoring-only alternative
needs_clarification -> Bragi intake, collecting_slots
chat               -> ordinary Bragi conversation
```

Diagnostics expose this classifier without invoking Hermes by default:

```json
{
  "mode": "goal_clarifier",
  "route": "bragi_goal_clarifier",
  "request_kind": "run_existing",
  "target_kind": "existing_task",
  "target_task_id": "daily_local_ai_security_briefing",
  "operation": {"action": "run_task", "task_id": "daily_local_ai_security_briefing"},
  "downstream_route": "yggdrasil_canonical_action"
}
```

Existing task changes never mutate task YAML directly from Bragi. They become
`propose_task_change` canonical intents, currently limited to
`topic_digest.modify_subjects.v1`, or a source-selection intake when natural
source names such as CISA or NVD need to be mapped to approved source IDs.

New tasks still use registered capability builders such as `topic_digest.v1`,
`server_health.v1`, `printer_supply_status.v1`, and `n8n_webhook.v1`. They
remain disabled/dry-run drafts, require user confirmation, pass through Heimdal
validation, and are forwarded to Yggdrasil only as deterministic canonical
actions.

Unsupported but reasonable automation ideas become capability proposals. These
are backlog records only: no task, approval, run, worker action, or Yggdrasil
request is created.

Unsafe requests are rejected before Heimdal/Yggdrasil. Examples include admin
approval handling, approval nonces, admin API keys, shell commands, Docker
socket access, Docker/service restarts, broad host filesystem changes, firewall
changes, raw webhook URLs, automatic update installation, purchases, and
secret-like material. Arbitrary URLs are not accepted into executable task
intents; public source URLs must go through the separate source-proposal review
path. Bragi may suggest a safer monitoring-plus-alert version, but it must not
forward the unsafe request.

User confirmation and Yggy approval remain separate:

```text
User confirmation
  confirms that Bragi understood the proposed canonical intent.

Yggy approval
  authorizes task changes or enablement according to policy.
```

Bragi must never say that a task is approved merely because the user confirmed
the intake summary.

If a draft request is missing required slots, Bragi returns a partial canonical
intent and asks for the missing details. Follow-up replies are merged into that
same intent and revalidated before anything reaches Yggdrasil.

If a request maps to a registered capability but the Bragi service is not
authorized to call Yggdrasil, Bragi should say that the understood automation
request could not be forwarded because the service is not authorized. That is
an authorization failure, not a capability failure.

### Optional Hermes Clarifier

Hermes can be enabled as an advisory local JSON clarifier:

```text
BRAGI_GOAL_CLARIFIER_ENABLED=false
BRAGI_GOAL_CLARIFIER_PROVIDER=hermes
BRAGI_GOAL_CLARIFIER_BASE_URL=http://host.docker.internal:8651
BRAGI_GOAL_CLARIFIER_MODEL=bragi-clarifier
BRAGI_GOAL_CLARIFIER_TIMEOUT=90
BRAGI_GOAL_CLARIFIER_API_KEY=...
BRAGI_GOAL_CLARIFIER_MAX_TURNS=6
BRAGI_GOAL_CLARIFIER_USE_LLM_JUDGE=false
```

This is disabled by default and must degrade safely if Hermes is unavailable,
unauthorized, returns invalid JSON, or proposes unsupported material. Hermes is
called only as a no-tools, JSON-only classifier. It receives redacted request
text, safe visible task summaries, aliases, allowed capability IDs, and the
deterministic classifier result. It must not receive `.env`, secrets, approval
nonces, admin keys, raw logs, webhook URLs, private file paths, or execution
authority. The clarifier key must be a dedicated low-risk key for this profile,
not the Yggdrasil action key, automation tool key, worker key, or admin key.
On the local deployment, the `bragi-clarifier` profile runs as a dedicated
local-only Hermes Agent API loop under the `hermes` user. It is kept separate
from the `:8642` Yggdrasil action API. The profile must explicitly disable
API-server tools with `platform_toolsets.api_server: [no_mcp]` and keep
`agent.disabled_toolsets` populated so tool descriptions and tool authority do
not enter the clarifier prompt.

Hermes output is validated with Pydantic and remains advisory. Existing
deterministic high-confidence operations and non-executable capability
proposals win unless Hermes supplies an unsafe finding or a candidate intent
that still goes through Heimdal. Candidate intents from Hermes are forced back
to `requires_user_confirmation=true` and
`user_confirmation_obtained=false`, then sent to
`POST /capabilities/validate-intent`. Unsafe slots, unknown capabilities, and
unsupported IDs are rejected by Heimdal or become non-executable backlog.

Goal clarification state is stored in existing `bragi_intake_records`, not a
parallel persistence model. `summary_json.goal` records non-secret metadata such
as request kind, target kind, candidate task IDs, assumptions, questions asked,
turn count, and the last classifier reason. The existing channel/user scoping,
statuses, confirmation rules, and follow-up commands continue to apply.

Relevant tests:

```bash
pytest bragi/tests
pytest automation-api/tests/test_capability_gateway.py automation-api/tests/test_task_templates.py
python scripts/validate_configs.py
```

## Read-Only Context

Bragi has a narrow context endpoint:

```text
POST /context/query
```

This endpoint is authenticated with `BRAGI_API_KEY` and is read-only. It lets
Bragi answer natural questions like:

```text
what can you automate right now?
what is pending?
what sources can I use for a brief?
what health checks do you know?
show recent run history
what does Yggy know about my AI stack?
```

The context layer may read:

- visible task summaries from `GET /tasks`
- recent run summaries from `GET /runs`
- service status from `GET /health`
- capability summaries from `GET /capabilities`
- approved-source research from `POST /research/query`
- approved sources from `GET /sources`
- approved health checks from `configs/metrics/services.yaml`
- approved n8n webhook IDs from `configs/n8n/webhooks.yaml`
- static non-secret Bragi memory from `configs/bragi/memory.yaml`
- user-scoped persistent Bragi memory from Bragi-owned database tables

The context layer must not return:

- approval nonces
- admin-only approval records
- admin API keys
- raw run logs
- registry URLs or webhook URLs
- tokens, passwords, cookies, private keys, or credentials

The context route improves conversation quality only. It is not approval,
execution, or source-of-truth state. Changes still go through the canonical
intent gateway, Yggdrasil, and Yggy approval path.

## Read-Only Research

Bragi can answer public-information questions through the Yggy research gateway:

```text
POST /research/query
GET /research/items
GET /sources
```

The gateway fetches only enabled `rss` and `http` source IDs from
`configs/sources/approved_sources.yaml`, blocks private and local network
addresses, stores bounded sanitized research items, and labels all external
content as untrusted data. Bragi receives this as conversation context only; it
does not gain arbitrary browsing, shell execution, Docker access, approval
authority, or task mutation authority.

For explicit research-backed topic digest draft requests, Bragi may call:

```text
POST /research/topic-digest-suggestion
```

That endpoint returns only suggested canonical-intent slots such as approved
`source_ids`, include filters, and research item IDs. Bragi must still send the
resulting canonical intent to Heimdal, show the user the confirmation summary,
and wait for user confirmation before Yggdrasil receives a deterministic
request.

Bragi may collect a new `topic_digest.v1` request across a natural multi-turn
conversation. This is intentionally limited to slot collection. The active
conversation must contain automation/briefing context and security or component
context, and the latest user message must advance the setup with details such as
daily/morning schedule, approved-source hints, vulnerability/patch/NVD scope, or
an explicit confirmation phrase like `so be it`. If the latest user message
describes desired sources in prose, Bragi must first search the approved source
registry and create an `awaiting_source_selection` intake. Once the user confirms
or narrows that source selection, Bragi generates a canonical intent, calls
Heimdal validation, and stores the normal confirmation intake. It must not
continue with a general-chat promise such as "I'll pass this to Yggdrasil" or
"you can expect this tomorrow."

Bragi can also answer natural source-catalog questions without creating an
automation draft:

```text
show sources for cybersecurity
what sources do you have for German business news?
find approved sources for vulnerability records
```

Those questions call only the read-only approved source registry. Bragi shows
source IDs, type, ingestion mode, AI-safe fit, region/language metadata when
available, and a metadata/link-only note for sources that must not be treated as
full-text fetch targets. This route is context only. It does not forward
anything to Yggdrasil and does not add arbitrary URLs to task YAML.

Bragi may also create a pending approved-source proposal when the user
explicitly asks to propose or register one public RSS/feed or website URL:

```text
propose https://example.org/security/feed.xml as an approved RSS source
register https://example.org/news as a source for operator review
```

This calls only `POST /sources/propose` with the tool key. The proposal is
review backlog, not an approved source. Bragi must not expose approval nonces,
must not apply the registry change, and must not attach the proposed URL to a
task. Operators review source proposals through `/ops/source-proposals` or the
admin API. Source proposals require public HTTPS URLs, no URL credentials, no
secret-like material, and no broad `web_query` source.

Confirmation phrases that close this conversational intake do not authorize
execution. They only trigger the first canonical intent display and an intake ID.
The normal `confirm intake <id>` response, or `confirm` while that intake remains
visible in the current conversation, is still required before Yggdrasil receives
a deterministic request. Yggy approval still controls whether the resulting
disabled draft can become active.

The intake store is not task authority. It holds only non-secret pre-execution
draft state:

```text
collecting
collecting_slots
awaiting_source_selection
awaiting_confirmation
confirmed
forwarded_to_yggdrasil
cancelled
expired
failed
```

Supported intake commands:

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

For brief-change requests that name sources naturally, such as:

```text
add CISA and NVD to the security brief
```

Bragi first searches `GET /sources` and shows matching approved source IDs,
source type, AI-safe fit, and ingestion mode. The reply creates an
`awaiting_source_selection` intake, not a canonical task-change intent. If the
user replies `confirm sources for intake <id>`, Bragi uses the default matches.
If the user replies `use sources 1 and 3 for intake <id>`, Bragi uses those
numbered approved source IDs. Bragi then creates a
`topic_digest.modify_subjects.v1` canonical intent and sends it through Heimdal
validation. The usual canonical intent confirmation and Yggy approval path still
apply after that. This keeps natural source matching out of Yggdrasil and
prevents arbitrary URLs or unsupported sources from being smuggled into task
YAML.

## Capability Proposals

Unsupported but reasonable automation ideas should not be forced into an
existing capability and should not disappear into ordinary chat. Bragi may draft
a non-executable capability proposal through:

```text
POST /capability-proposals/draft
GET /capability-proposals
GET /capability-proposals/{id}
POST /capability-proposals/{id}/close
POST /capability-proposals/{id}/accept
POST /capability-proposals/{id}/reject
```

Printer supply monitoring is now a registered capability:

```text
printer_supply_status.v1
```

Bragi can draft it only when the request names approved printer IDs from
`configs/printers/printers.yaml`; otherwise it asks for the missing
`printer_ids` slot. The capability uses read-only HTTP JSON supply endpoints and
does not scan the LAN, use SNMP directly, submit print jobs, or administer
printers.

Example of a still-unsupported idea:

```text
Monitor printer page counts through SNMP.
```

This may become a review object such as `printer_page_count.v1`, with purpose,
required inputs, likely approval level, safety rules, and non-goals. It does not
create a task, approval, run, task template, or Yggdrasil request. Tool role may
draft and list proposals. Admin role may accept, reject, or close them. There is
no `apply` endpoint; implementation still requires a human/Codex change to the
capability registry, templates, worker handler, docs, and tests.

After an operator accepts a capability proposal, the local `/ops` UI can create
an `implementation_planned` record. That record is a checklist for engineering
work: likely files to change, required operator decisions, security boundaries,
and acceptance tests. It remains backlog only. It does not create a task,
approval, run, task template, worker handler, or Yggdrasil request. Bragi may
report this proposal and plan status through read-only context so the user can
ask what happened with an automation idea, but Bragi still cannot implement,
approve, or execute it.

The `/ops` UI can queue a separate `capability_implementation_run` handoff for a
planned proposal. This is still not model-facing execution. It only records that
the local operator intends to run the host-side implementation CLI:

```bash
python scripts/implement_capability_plan.py --proposal-id <proposal-id>
```

That CLI may invoke a dedicated Hermes profile named `capability-implementer`
to edit the repository and then create a local commit after validation passes.
Bragi, Open WebUI, Yggdrasil, and model-facing tool keys do not receive admin
keys, approval nonces, Docker access, deployment authority, or the ability to
run this implementation path. See `docs/CAPABILITY_IMPLEMENTATION_AGENT.md`.

Unsafe requests still stay rejected instead of becoming proposals. For example,
requests to restart Docker, reorganize arbitrary server files, change firewall
rules, rotate credentials, or execute shell commands must not be forwarded to
Yggdrasil and must not become model-facing executable work.

For incomplete canonical intents, Bragi stores a `collecting_slots` intake. The
user can continue later by including the intake ID with the missing information,
for example:

```text
use docker_blog and send it to briefings for intake bragi_intake_...
```

Bragi merges the details, revalidates through Heimdal, and only then shows the
normal confirmation summary. This makes the natural intake flow independent of
whether Open WebUI or Discord keeps the previous assistant message in context.

Every incomplete-intake reply must state the missing slots and show two explicit
choices:

```text
Complete it: reply with the missing details and include `for intake <id>`.
Delete it: reply `delete intake <id>` or `cancel intake <id>`.
```

If the incomplete request is still present in the current conversation, Bragi
may also accept `delete it` as a shortcut. Deleting an intake only cancels the
pre-execution draft state; nothing has been sent to Yggdrasil.

The user may also resume intake handling without waiting for a timed reminder:

```text
continue request
continue intake bragi_intake_...
resume request bragi_intake_...
```

If exactly one active intake exists, Bragi shows that intake's next safe action.
If more than one active intake exists, Bragi lists them and asks the user to pick
one by ID. This is a read/management path only; it must not confirm, approve,
run, or forward anything to Yggdrasil.

Intake visibility is scoped to the logical user/audience, not to raw Discord or
Open WebUI identifiers. Channel adapters map humans to an audience through
`configs/channels.yaml`, for example `local_user`. Bragi may resume same-user
intakes across configured channels, so an intake created from Discord can be
continued from Open WebUI if both channels map to the same audience. Bragi must
not show, continue, confirm, or delete another audience's intake; the response
should be equivalent to `intake not found`.

Pending intake listings may be filtered by channel wording:

```text
show pending Discord requests
show pending Open WebUI requests
show pending requests in this channel
show all my pending requests
continue Discord request
continue current request
```

Listings show the origin channel, created/updated time, status, and next needed
human action. These fields are context only and do not grant approval authority.

Bragi stores bounded follow-up metadata for active intake states:

```text
followup.enabled
followup.channel
followup.last_reminded_at
followup.reminder_count
followup.max_reminders
followup.next_reminder_at
```

The runtime representation lives inside the intake summary JSON as non-secret
metadata. Bragi exposes:

```text
GET /intakes/pending-followups
POST /intakes/followups/mark-sent
```

The channel bridge may poll the first endpoint and call the second endpoint
after it posts a reminder. These endpoints do not confirm, approve, run, or
forward anything to Yggdrasil.

See `docs/RESEARCH_GATEWAY.md`.

## Route Diagnostics

Bragi exposes a read-only route diagnostic endpoint:

```text
POST /diagnostics/route
```

The endpoint accepts either:

```json
{"text": "send daily brief now"}
```

or:

```json
{"messages": [{"role": "user", "content": "send daily brief now"}]}
```

It returns the request mode, proposed internal route, and any candidate
canonical operation or intent. It does not call Ollama, Heimdal, Yggdrasil,
Discord, or the automation API, and it removes the raw `user_request` field from
candidate intents.

For quick troubleshooting from Open WebUI, ask Bragi:

```text
diagnose route: how can i add a new subject to the brief?
diagnose route: send daily brief now
diagnose route: draft a weekday 08:00 local AI security briefing
```

This is meant to make routing decisions visible without weakening the execution
boundary. Diagnostics are not approval, execution, or a source of authority.
For context questions, diagnostics report `general_chat_with_context` and the
context categories that would be loaded, but the diagnostic itself does not load
that context.

## Channel Audit Logging

Human-channel adapters such as the Discord channel bridge write redacted channel
ingress events to:

```text
POST /channels/events
```

The bridge uses `AUTOMATION_CHANNEL_BRIDGE_API_KEY`, a dedicated low-privilege
key separate from the model tool, worker, and admin keys. That role can record
channel events but cannot approve tasks, mutate task state, claim worker runs,
or access admin-only approval data.

Channel events are stored in the Yggy `audit_events` table with
`resource_type=channel_event`. They record hashed channel and author IDs, the
channel config ID, route, required capability, forwarding decision, blocked
reason, and short redacted previews. They do not store Discord bot tokens,
webhook URLs, approval nonces, passwords, full message archives, or attachment
contents.

Admins can inspect channel ingress with:

```text
GET /channels/events
GET /channels/events/{event_id}
```

These endpoints are intentionally admin-read only. Bragi memory and Open WebUI
Knowledge are not used as audit stores.

## Yggdrasil Boundary

Bragi forwards accepted requests to:

```text
POST /v1/yggdrasil/canonical-actions
```

That endpoint accepts only structured `draft_task_from_template` requests for
the milestone templates and structured task operations. It rejects raw natural
language.

Supported canonical operations:

```text
draft_task_from_template
propose_task_change
list_tasks
show_task
run_task
pause_task
```

`propose_task_change` is currently limited to
`topic_digest.modify_subjects.v1`. It can add/remove approved source IDs and
include-filter terms for an existing `topic_digest` task, but it creates only a
pending task-change proposal. It does not approve, apply, enable, or run the
task. The model-facing response intentionally does not show the approval nonce;
review and approval stay in the local `/ops` UI or admin CLI.

Run and pause operations still go through the automation API, so task approval,
dry-run state, rate limits, active-run locks, and role checks remain
authoritative there.

## Non-Secret Memory

Bragi has two memory sources. Static operator-curated memory lives in:

```text
configs/bragi/memory.yaml
```

This file is mounted read-only into the Bragi container. It is conversation
context only, not execution state and not approval authority.

Allowed examples:

- preferred language
- message style
- default timezone
- non-secret service aliases
- automation preferences

Forbidden examples:

- API keys
- tokens
- passwords
- webhook URLs
- approval nonces
- cookies
- private keys

If the memory file contains secret-like keys or values, Bragi ignores it.

Persistent Bragi memory lives in Bragi-owned database tables:

```text
bragi_memory_records
bragi_memory_events
```

The Docker deployment should point `BRAGI_MEMORY_DATABASE_URL` at the same MySQL
server used by Yggy, but these tables remain Bragi-owned and are not automation
task state.

Memory endpoints:

```text
POST /memory/query
POST /memory/propose
POST /memory/commit
POST /memory/forget
```

Rules:

- memory writes require an explicit user instruction such as `Remember that ...`
- Bragi creates a pending memory proposal first
- the user must reply `remember` before the proposal becomes active
- memory records are scoped by `user_id`
- memory may hold preferences, aliases, routines, service aliases,
  notification style, project interests, defaults, and notes
- memory must not hold API keys, passwords, tokens, webhook URLs, approval
  nonces, cookies, private keys, credentials, live approval decisions, or raw
  private message archives
- memory can be inspected with `what do you remember about me?`
- memory can be forgotten with `forget ...`
- memory is conversation context only and is never approval, execution,
  credential, or task-state authority

Examples:

```text
Remember that I prefer short Discord alerts unless something failed.
remember
what do you remember about me?
forget Discord alerts
```

If a user asks Bragi to remember secret-like material, Bragi refuses and points
the user to `.env`, Docker secrets, n8n credentials, or a local secret manager.

The identity registry lives at:

```text
configs/identities.yaml
```

It defines stable local user IDs and channel subject references for future
channel adapters. Deployment-specific subject values should be referenced by
environment variable name, not committed as secrets.

## Channel Registry

Human-facing transports are configured in:

```text
configs/channels.yaml
```

The registry is declarative, non-secret, and versioned. It defines which
channels may talk to Bragi and which safe Bragi capabilities are available per
channel.

Initial capabilities are:

```text
chat
context
memory
source_proposal
draft_task
task_read
run_l1
pause_l1
```

All model-facing channels must keep `allow_approvals: false`. Approval and
admin authority remain in the local ops UI, admin CLI, and automation API.

Discord channels use environment references instead of raw identifiers or
credentials in YAML:

```yaml
channel_id_ref: DISCORD_HOME_CHANNEL
allowed_user_ids_ref: DISCORD_ALLOWED_USER_IDS
```

`DISCORD_ALLOWED_USER_IDS` is a comma-separated allowlist. If it is empty, the
channel allowlist still applies, but per-user restriction is not enforced.

## Discord Transport

Bragi exposes:

```text
POST /channels/discord/message
```

This is an ingress endpoint for a Discord bridge. The bridge passes the channel
ID, author ID, message content, optional message history, and attachment
metadata. Bragi returns a reply and classification metadata; it does not receive
the Discord bot token and does not send Discord messages itself.

The endpoint:

- requires `BRAGI_API_KEY`
- accepts only configured Discord channels
- optionally enforces `DISCORD_ALLOWED_USER_IDS`
- ignores bot messages
- strips bot mentions before routing
- rejects attachments by default
- rejects overlong messages according to the channel registry
- refuses admin keys, tokens, approval nonces, and approval/rejection handling
- routes only configured safe capabilities for that channel

Discord can be used for natural conversation, context questions, memory
proposals, task reads, and configured low-risk L1 runs. It cannot approve
tasks, expose nonces, handle admin secrets, or bypass the Yggy automation API.

The preferred runtime transport is the repository-owned `channel-bridge`
service:

```text
Discord
  -> channel-bridge
      -> Bragi /channels/discord/message
          -> Heimdal/Yggdrasil/Yggy as needed
```

`channel-bridge` owns the Discord bot token and posts Bragi's replies back to
Discord with mentions disabled. It reads `configs/channels.yaml`, enforces the
configured channel and optional author allowlist before contacting Bragi, passes
only bounded recent history for confirmation continuity, and does not receive
the Yggy admin key, worker key, database URL, Discord webhooks, approval nonces,
or Docker access.

Do not run the legacy Hermes Discord gateway and `channel-bridge` against the
same bot token at the same time, or both can answer the same message.

## Open WebUI

Use Bragi as a separate OpenAI-compatible model/provider. Keep the existing
Yggdrasil model strict and deterministic.

Do not attach these to Bragi:

- Workspace Python tools
- shell or terminal tools
- Docker socket access
- filesystem write tools
- admin API key
- approval nonces
- webhook URLs, tokens, passwords, cookies, or private keys

Bragi needs only the model-facing automation tool key and, if configured, the
Yggdrasil action API key.
