# Printer Status Exporter

The printer status exporter is a narrow read-only adapter for printer supply
status. It exists so Yggy printer automations can call one predictable internal
HTTP endpoint instead of giving a model or worker broad printer, LAN, shell, or
Docker access.

Endpoints:

- `GET /health`: exporter process health.
- `GET /printers`: configured printer inventory without secrets.
- `GET /printers/{printer_id}/supplies`: normalized supply levels for one
  configured printer.

Supported source types:

- `static_json`: static non-secret sample data, useful for dry-run validation.
- `http_json`: bounded HTTP GET to one configured upstream URL whose response
  already contains supplies, consumables, or levels.

The exporter does not scan the LAN, use SNMP, submit print jobs, administer
printers, read host files, run shell commands, or store credentials. Upstream
URLs must be configured by the operator in YAML and must not contain username or
password material.
