# Open WebUI and Hermes Integration

The existing setup has Open WebUI connected to Hermes/yggdrasil. Preserve that separation. Add this control plane as a narrow OpenAPI tool server only.

## Tool Server

Expose:

```text
http://127.0.0.1:8088/openapi.json
```

or an equivalent local-only URL reachable by Open WebUI/Hermes.

Configure only:

```text
X-Automation-Api-Key: <AUTOMATION_TOOL_API_KEY>
```

Never configure `AUTOMATION_ADMIN_API_KEY` in Open WebUI or Hermes.

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
