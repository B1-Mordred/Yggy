# Automation API

FastAPI service that owns task validation, approval state, run logs, audit events, and the OpenAPI tool surface for yggdrasil.

The API uses MySQL in Docker Compose through `DATABASE_URL`. Tests use SQLite for local isolation.
