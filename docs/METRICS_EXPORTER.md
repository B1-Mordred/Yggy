# Metrics Exporter

The metrics exporter provides narrow read-only service visibility for Yggy.

It exists so server-health automations can inspect a bounded set of local service
health endpoints without access to the Docker socket, shell commands, host
network mode, or the host filesystem.

## Endpoints

```text
GET /health
GET /metrics/services
```

The service is internal-only in Docker Compose and is intended to be queried by
the automation worker:

```text
http://metrics-exporter:8090/metrics/services
```

## Configuration

Configured checks live in:

```text
configs/metrics/services.yaml
```

Supported check types:

- `http_health`: HTTP status check.
- `worker_heartbeat`: reads the automation API `/health` worker heartbeat.
- `ollama_tags`: reads Ollama `/api/tags` and requires at least one model.

The exporter returns only check metadata, status code, latency, and bounded
type-specific facts such as model count or worker heartbeat age. It does not
return environment variables, headers, request bodies, secrets, container
metadata, process lists, file contents, or Docker state.

## Security Rules

- No Docker socket mounts.
- No privileged container.
- No host network mode.
- No broad host filesystem mounts.
- No shell execution.
- No secrets in the YAML inventory.
- No public port publishing by default.
