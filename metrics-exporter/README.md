# Metrics Exporter

The metrics exporter is a narrow read-only service-health adapter for Yggy.

It does not use the Docker socket, shell commands, host networking, privileged
containers, or host filesystem mounts. It reads a static YAML inventory and
performs bounded HTTP GET checks against allowlisted local service endpoints.

Endpoints:

- `GET /health`: exporter process health.
- `GET /metrics/services`: bounded service inventory and current HTTP check
  results.

The exporter is intended for the automation worker's `server_health` task. It is
not a policy authority and it does not expose secrets.
