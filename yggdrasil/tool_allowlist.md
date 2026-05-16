# yggdrasil Tool Allowlist

Expose only the automation API OpenAPI server to yggdrasil.

Allowed:

- `GET /health`
- `GET /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/draft`
- `POST /tasks/{task_id}/request-approval`
- `POST /tasks/{task_id}/pause` for L0/L1
- `POST /tasks/{task_id}/run` for approved L0/L1 or dry-run
- `GET /topics`
- `POST /topics/draft`
- `GET /runs`
- `GET /runs/{run_id}`

Not allowed:

- arbitrary Python execution
- shell tools
- Docker socket tools
- broad filesystem tools
- direct Discord webhook access
- admin approval endpoints
- admin API key access
