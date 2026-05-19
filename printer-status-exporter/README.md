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

Configuration lives in:

```text
configs/printer-status-exporter/printers.yaml
```

The automation control plane allowlist lives separately in:

```text
configs/printers/printers.yaml
```

Use the helper script from the repository root to update both registries
together:

```bash
python scripts/configure_printer_status.py \
  --printer-id office_laser \
  --name "Office Laser" \
  --upstream-url http://printer-adapter.local/supplies \
  --threshold 20
```

For static dry-run data:

```bash
python scripts/configure_printer_status.py \
  --printer-id office_laser_dry_run \
  --name "Office Laser Dry Run" \
  --static-supply "Black toner=75" \
  --static-supply "Cyan toner=64"
```

Keep the control-plane URL pointed at this exporter, for example:

```text
http://printer-status-exporter:8091/printers/office_laser/supplies
```

Validate the mapping after edits:

```bash
python scripts/validate_printer_status.py
python scripts/validate_configs.py
```
