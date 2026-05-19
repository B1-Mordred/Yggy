# Open WebUI and Hermes Integration

The existing setup has Open WebUI connected to Hermes/yggdrasil. Preserve that separation. Add this control plane as a narrow OpenAPI tool server only.

Bragi is a separate optional OpenAI-compatible model/provider for natural human
conversation. Keep it separate from the strict Yggdrasil profile. Bragi should
not receive Workspace Python tools, shell tools, Docker tools, filesystem write
tools, admin keys, approval nonces, webhook URLs, or secrets.

See `docs/BRAGI_HEIMDAL_INTEGRATION.md` for the Bragi -> Heimdal -> Yggdrasil
boundary model.

Discord should use Bragi through the first-class channel bridge and Bragi's
narrow channel adapter, not through the strict Yggdrasil profile:

```text
Discord -> channel-bridge -> POST http://bragi:8650/channels/discord/message
```

The Discord bridge should send channel ID, author ID, message content, optional
short history, and attachment metadata. Bragi returns the text reply to post.
Do not give Bragi the Discord bot token, webhook URLs, admin API key, approval
nonces, shell tools, Docker tools, or filesystem write tools.

If the existing Discord gateway can only speak to an OpenAI-compatible model,
that profile can be used as a temporary compatibility bridge. The preferred
production path is the repository-owned `channel-bridge` service. For the
temporary Hermes profile, configure it as a thin Bragi client instead of a
general-purpose agent:

```yaml
name: Bragi
display_name: Bragi
profile_name: Bragi
alias: Bragi

model:
  default: bragi
  provider: custom:bragi
  base_url: http://127.0.0.1:8650/v1
  api_mode: chat_completions

providers:
  bragi:
    name: bragi
    base_url: http://127.0.0.1:8650/v1
    key_env: BRAGI_API_KEY
    api_mode: chat_completions
    model: bragi

toolsets: []
fallback_providers: []
```

The Discord profile `.env` may contain `DISCORD_BOT_TOKEN`,
`DISCORD_ALLOWED_USERS`, and `BRAGI_API_KEY`. It must not contain the Yggy admin
key, worker key, approval nonces, task secrets, or webhook URLs for Bragi.

## Bragi Provider

When the Bragi service is running, add it to Open WebUI as a separate
OpenAI-compatible provider:

```text
Base URL: http://bragi:8650/v1
Model: bragi
API key: BRAGI_API_KEY from the private .env
```

If Open WebUI is not on Yggy's Docker network, expose Bragi intentionally with
`docker-compose.lan.yml` and use:

```text
Base URL: http://<lan-ip>:8650/v1
```

Bind `BRAGI_LAN_PUBLISHED_HOST` to the specific LAN address, not `0.0.0.0`.

Use Bragi for natural requests:

```text
Bragi, can you keep an eye on my AI server and tell me if something breaks?
Bragi, draft a weekday 08:00 local AI security briefing to Discord, but keep it disabled.
Bragi, add Docker security updates to the daily brief.
```

Bragi will ask for confirmation, send only canonical intents to Heimdal, and
forward only accepted deterministic actions to Yggdrasil. User confirmation is
not Yggy approval. Existing-brief changes become Yggy task-change proposals;
Bragi does not approve or apply them and does not expose approval nonces.

Bragi's deterministic goal router first decides whether a request targets an
existing task, a new task from a registered capability, a new non-executable
capability proposal, unsafe work, missing clarification, or ordinary chat. This
keeps natural conversation in Bragi while preserving Yggdrasil as a strict
canonical-action endpoint.

Ordinary chat, such as greetings or general questions, stays inside Bragi's
no-tool chat fallback. Bragi should not describe normal conversation as a
Yggdrasil routing failure. Only registered automation capabilities enter the
Heimdal/Yggdrasil path.

Keep the existing Yggdrasil provider available for strict commands:

```text
Yggdrasil, list my automation tasks.
Yggdrasil, show task daily_local_ai_security_briefing.
Yggdrasil, run approved task daily_local_ai_security_briefing dry-run.
```

## Tool Server

Expose from Open WebUI:

```text
http://automation-api:8088/openapi.json
```

or an equivalent local-only URL reachable by Open WebUI/Hermes.

Configure only:

```text
X-Automation-Api-Key: <AUTOMATION_TOOL_API_KEY>
```

Never configure `AUTOMATION_ADMIN_API_KEY` in Open WebUI or Hermes.

For this host, the repeatable configuration helper is:

```bash
python scripts/configure_openwebui_tool_server.py
docker restart open-webui
```

The helper stores only the model-facing `AUTOMATION_TOOL_API_KEY` in Open WebUI's tool-server config, attaches the tool server to the `webui`/Yggdrasil model, and filters exposed operations to the low-privilege automation API allowlist.

The installed Yggdrasil action endpoint is intentionally scoped to this project only. Open WebUI-facing Yggdrasil requests should not be routed to older Hermes brief-management, profile-management, host-management, or proposal-queue domains.

Yggdrasil may list and explain the local task template catalog for drafting
help through the automation API's `/task-templates` endpoints. Templates are not
a separate execution path. Rendered templates remain disabled/dry-run task YAML
and must go through automation API validation, approval, and enablement.

For existing tasks, Yggdrasil may create task-change proposals through
`POST /tasks/{task_id}/propose-change` and inspect them through
`GET /task-change-proposals`. Do not expose proposal approve/reject/apply
operations or the admin API key to Open WebUI.

## Workspace Tools Warning

Do not implement this system by giving Open WebUI Workspace Tools or Functions broad Python execution. Treat broad Python tools as shell-level trust. Use the automation API as the policy boundary instead.

## Knowledge

Knowledge may contain non-secret operational context only.

Recommended Knowledge documents:

- `personal_routines.md`
- `server_inventory.md`
- `automation_policy.md`
- `discord_notification_style.md`
- `approved_sources.md`
- `project_watchlist.md`

Do not include credentials, API keys, webhook URLs, cookies, tokens, private keys, or recovery codes.

## Approved Sources

Use Knowledge files for preferences and non-secret project notes only. The
automation worker source allowlist lives in Git at
`configs/sources/approved_sources.yaml`, and topic digest tasks must reference
those entries with `source_id`.

Yggdrasil should not invent broad `web_query` sources for topic digests. Drafts
should use approved feed IDs such as `open_webui_releases`, `ollama_releases`,
`n8n_releases`, and `docker_blog`, then ask for approval before enabling or
changing recurring delivery.

For public-source questions, Bragi should use the Yggy read-only research
gateway rather than direct arbitrary browsing. The gateway fetches only enabled
approved source IDs and returns sanitized context. See
`docs/RESEARCH_GATEWAY.md`.

For research-backed draft requests, Bragi may use the research gateway to
suggest topic-digest source IDs and filters, but the resulting automation still
must be represented as a canonical intent, confirmed by the user, validated by
Heimdal, forwarded deterministically to Yggdrasil, and approved through Yggy
policy before live delivery.

For recurring task shapes, prefer the reviewed templates in
`configs/task_templates/` and the workflow in `docs/TASK_TEMPLATES.md`.
