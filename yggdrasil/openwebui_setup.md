# Open WebUI Setup

1. Start the automation API only after reviewing this repository.
2. Export the OpenAPI spec with `scripts/export_openapi.sh` or use `http://127.0.0.1:8088/openapi.json`.
3. In Open WebUI, add the automation API as a narrow OpenAPI tool server.
4. Configure only `AUTOMATION_TOOL_API_KEY` for yggdrasil.
5. Do not configure `AUTOMATION_ADMIN_API_KEY` in Open WebUI, Hermes, prompts, Knowledge, or chat.
6. Keep Knowledge documents non-secret and procedural.

Do not give Open WebUI Workspace Tools or Functions broad Python execution privileges for this system.
