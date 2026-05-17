# Open WebUI and Hermes Integration

The existing setup has Open WebUI connected to Hermes/yggdrasil. Preserve that separation. Add this control plane as a narrow OpenAPI tool server only.

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
