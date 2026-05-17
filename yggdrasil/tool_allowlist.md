# yggdrasil Tool Allowlist

Expose only the automation API OpenAPI server to yggdrasil.

This Yggdrasil profile is dedicated exclusively to the personal automation control plane. Do not expose older Hermes brief-management, profile-management, host-management, terminal, Docker, filesystem, or proposal-queue tools through this profile.

Allowed:

- `GET /health`
- `GET /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/draft`
- `GET /task-templates`
- `GET /task-templates/{template_id}`
- `POST /task-templates/{template_id}/draft`
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
