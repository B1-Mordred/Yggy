# Codex instructions: external automations to adapt into Yggy capabilities

Generated: 2026-05-24

Target repository: `B1-Mordred/Yggy`

Target branch: `dev`

## Purpose

This brief identifies public automation projects with accessible source code that can be transformed into narrow, policy-gated Yggy capabilities without weakening the existing Bragi -> Heimdal -> Yggdrasil -> automation-api boundary.

The projects below are not instructions to vendor large third-party systems into Yggy. Treat them as design references and, only where appropriate, small source references. Prefer clean-room, minimal implementations that match Yggy's existing task-template, capability-registry, validation, worker-handler, and test patterns.

## Required reading before implementation

Codex must read these repository files before making code changes:

- `README.md`
- `SECURITY.md`
- `docs/BRAGI_HEIMDAL_INTEGRATION.md`
- `docs/CAPABILITY_IMPLEMENTATION_AGENT.md`
- `docs/TASK_TEMPLATES.md`
- `docs/TASK_SCHEMA.md`
- `configs/capabilities.yaml`
- `configs/policies.yaml`
- `automation-api/app/services/capability_gateway.py`
- `automation-api/app/services/task_template_service.py`
- `scripts/task_template_lib.py`
- `automation-worker/worker/main.py`

Also inspect nearby implemented capabilities before adding any new one:

- `configs/task_templates/printer_supply_status.yaml`
- `configs/task_templates/tls_certificate_expiry.yaml`
- `automation-worker/worker/handlers/printer_supply_status.py`
- `automation-worker/worker/handlers/tls_certificate_expiry.py`
- `automation-worker/tests/test_printer_supply_status.py`
- `automation-worker/tests/test_tls_certificate_expiry.py` if present; otherwise create the analogous tests for new handlers.

## Non-negotiable Yggy compatibility rules

Every implementation in this document must preserve these rules:

1. **No arbitrary shell execution.** Do not use `subprocess`, shell scripts, `df`, `du`, `curl`, `docker`, `smartctl`, or host commands inside worker handlers or model-facing paths.
2. **No Docker socket.** Do not add Docker socket mounts, Docker API clients, container label discovery, container restarts, image pulls, image updates, or Compose control.
3. **No arbitrary URLs in executable task slots.** All external targets must come from explicit checked-in registries and must be validated by ID.
4. **No secrets in YAML, prompts, docs, logs, task configs, or memory.** If a future capability needs credentials, stop and require a separate operator-owned secret registry design.
5. **New task templates must render `enabled: false` and `runtime.dry_run: true` by default.** The template renderer must force the usual safe policy fields.
6. **Bragi confirmation is not Yggy approval.** Do not add routes that approve, enable, deploy, or run new automations merely because the user confirmed a draft.
7. **External content is untrusted data.** Fetched webpages, registry responses, headers, and API payloads must never become command authority.
8. **Worker output must be bounded.** Store short summaries, hashes, counts, status metadata, and small excerpts only. Do not persist full pages, large responses, credentials, cookies, or private content.
9. **One capability per implementation change.** Keep each PR/commit narrowly scoped unless the operator explicitly requests a combined implementation.
10. **Respect licenses.** Preserve notices if code is copied. Prefer reimplementation from concepts rather than copying third-party source.

## Candidate ranking

| Rank | Proposed Yggy capability | Source project(s) | Why this fits Yggy | Initial decision |
|---:|---|---|---|---|
| 1 | `container_image_update_watch.v1` | Diun: https://github.com/crazy-max/diun | Notify-only registry metadata checks. High value for a local stack. Can be implemented without Docker socket or image updates. | Implement first. |
| 2 | `web_content_change.v1` | changedetection.io: https://github.com/dgtlmoon/changedetection.io | Deterministic HTTP fetch, extraction, hash comparison, and anomaly notification. Fits approved-source and dry-run task patterns. | Implement second. |
| 3 | `scheduled_task_watchdog.v1` | Healthchecks: https://github.com/healthchecks/healthchecks | Missing-run monitoring maps naturally to Yggy's own run history. No public ping endpoint required for v1. | Implement third. |
| 4 | `endpoint_probe.v1` | Gatus: https://github.com/TwiN/gatus; Prometheus Blackbox Exporter: https://github.com/prometheus/blackbox_exporter | Useful if Yggy needs approved external HTTP/TCP/DNS probes beyond existing `server_health` and `tls_certificate_expiry`. | Implement only after confirming it does not duplicate current health capabilities. |
| 5 | `storage_usage.v1` | Glances: https://github.com/nicolargo/glances | Yggy already has a capability gap for storage monitoring. A safe v1 can check approved mount IDs through a read-only exporter or explicit read-only mounts. | Implement carefully after registry/exporter design. |
| Park | `smart_drive_health.v1` | Scrutiny: https://github.com/AnalogJ/scrutiny | SMART monitoring is valuable, but the reference design uses host device access and smartctl/smartd patterns that conflict with Yggy's worker boundary. | Do not implement directly. Propose only as future exporter-backed gap. |
| Park | `http_security_headers.v1` | Mozilla HTTP Observatory: https://github.com/mozilla/http-observatory | Header/security observations are useful, but the repository is archived/deprecated. | Park; a minimal approved-endpoint header check may be designed later. |

## Global implementation pattern for every accepted capability

For each capability that Codex implements, make the smallest set of repository changes needed to satisfy this shape:

1. Add a registry entry to `configs/capabilities.yaml`:
   - unique capability ID ending in `.v1`
   - `maps_to_task_type`
   - `maps_to_template`
   - `deterministic_action: draft_task_from_template`
   - only `L0_READ_ONLY` and/or `L1_NOTIFY_ONLY` unless explicitly justified
   - required and optional slots
   - registry ID allowlists or `allow_any_approved_*` only when the target registry is strict
   - safety rules and unsafe keywords
2. Add a disabled dry-run task template under `configs/task_templates/<task_type>.yaml`.
3. Add any required target registry under `configs/<domain>/...yaml`.
4. Update task-template rendering in `scripts/task_template_lib.py` and any render/list CLI tests.
5. Update automation API validation and schema support:
   - task validation rejects unknown IDs, arbitrary URLs, unsafe policy flags, malformed thresholds, invalid cron/timezone, and secret-like values.
   - Heimdal validation rejects unsupported slot values before Yggdrasil receives anything.
6. Add a worker handler under `automation-worker/worker/handlers/<task_type>.py`.
7. Wire the handler into `automation-worker/worker/main.py` dispatch.
8. Add focused worker tests with mocked network/API/state access.
9. Add focused API tests:
   - `automation-api/tests/test_capability_gateway.py`
   - `automation-api/tests/test_task_templates.py`
   - `automation-api/tests/test_task_validation.py`
10. Add or update Yggdrasil/Bragi route tests only when natural-language drafting should recognize the new capability.
11. Update docs:
   - `docs/TASK_TEMPLATES.md`
   - `docs/TASK_SCHEMA.md`
   - `docs/BRAGI_HEIMDAL_INTEGRATION.md`
   - `configs/README.md` if a new registry is introduced.
12. Run validation:

```bash
python scripts/validate_configs.py
pytest automation-api/tests/test_capability_gateway.py automation-api/tests/test_task_templates.py automation-api/tests/test_task_validation.py
pytest automation-worker/tests yggdrasil/tests bragi/tests
```

If a test target is absent, run the closest existing package tests and add the missing focused tests.

---

# Candidate 1: `container_image_update_watch.v1`

## Source automation reference

Reference project: Diun, https://github.com/crazy-max/diun

Useful source concepts:

- Watch configured container images for new tags or digest changes.
- Notify when an update is available.
- Keep the behavior notify-only, not update/apply.

Yggy must not copy Diun's full service model. Implement only a bounded registry metadata checker.

## Yggy capability goal

Draft disabled, dry-run tasks that check approved OCI image references and notify when an approved tag's digest changes or an approved repository exposes a newer matching tag.

## Capability registry entry

Add to `configs/capabilities.yaml`:

```yaml
  - id: container_image_update_watch.v1
    purpose: Monitor explicitly approved container image references for tag or digest changes and notify without pulling, running, or updating images.
    maps_to_task_type: container_image_update_watch
    maps_to_template: container_image_update_watch
    deterministic_action: draft_task_from_template
    allowed_approval_levels:
      - L0_READ_ONLY
      - L1_NOTIFY_ONLY
    default_approval_level: L1_NOTIFY_ONLY
    allowed_output_targets:
      - alerts
      - briefings
    required_slots:
      - task_id
      - name
      - cron
      - timezone
      - image_ids
      - output_target
    optional_slots:
      - notification_policy
      - notify_on_first_seen
    safety_rules:
      - Image IDs must come from the approved image registry.
      - The worker may query public registry metadata only.
      - The worker must not pull images, update containers, use Docker, inspect local containers, or discover images from labels.
      - Rendered tasks must remain disabled and dry-run by default.
      - Alerts must go only to whitelisted output targets.
    unsafe_keywords:
      - docker socket
      - docker pull
      - docker run
      - docker compose
      - restart container
      - update container
      - watchtower
      - auto update
      - container labels
      - arbitrary registry
      - registry password
```

## New approved image registry

Create `configs/container-images/images.yaml`:

```yaml
version: 1
images:
  - image_id: open_webui_main
    name: Open WebUI main image
    registry: ghcr.io
    repository: open-webui/open-webui
    tag: main
    enabled: true
    auth: none
    allowed_digest_algorithms:
      - sha256
  - image_id: n8n_latest
    name: n8n latest image
    registry: docker.io
    repository: n8nio/n8n
    tag: latest
    enabled: false
    auth: none
    allowed_digest_algorithms:
      - sha256
```

Validation rules:

- `image_id` must be slug-like.
- `registry`, `repository`, and `tag` must be non-empty and must not contain credentials.
- Only `auth: none` is supported in v1.
- Disabled registry entries cannot be used in rendered tasks.
- Do not allow raw image references in task slots; tasks store expanded metadata from approved IDs.

## Task template shape

Create `configs/task_templates/container_image_update_watch.yaml` with safe defaults:

```yaml
id: container_image_update_watch
name: Container Image Update Watch
description: Render disabled dry-run tasks that monitor approved public container image references for digest changes.
task_type: container_image_update_watch
default_approval_level: L1_NOTIFY_ONLY
allowed_output_targets:
  - alerts
  - briefings
required_fields:
  - id
  - name
optional_fields:
  - image_ids
  - cron
  - timezone
  - output_target
  - owner
  - created_by
  - notify_on_first_seen
safety_notes:
  - Only approved image IDs may be rendered into task configs.
  - The worker queries registry metadata only; it must not pull, run, update, or inspect containers.
  - Rendered tasks must remain disabled and dry-run by default.
defaults:
  enabled: false
  owner: local_user
  created_by: yggdrasil
  trigger:
    kind: schedule
    cron: "0 8 * * *"
    timezone: Europe/Berlin
  container_images: []
  output:
    channel: discord
    target: alerts
    format: "anomalies only"
  policy:
    approval_level: L1_NOTIFY_ONLY
    require_sources: false
    max_runs_per_hour: 4
    max_runs_per_day: 12
    min_seconds_between_runs: 300
    allow_external_side_effects: false
    allow_shell: false
    allow_docker_socket: false
    allow_filesystem_write: false
  runtime:
    dry_run: true
    timeout_seconds: 30
    retry_count: 1
  notifications:
    on_success: false
    on_failure: true
    on_empty_result: false
    quiet_hours:
      enabled: false
    collapse_repeated_failures: true
    failure_collapse_window_minutes: 120
```

Rendered tasks should include expanded image records, for example:

```yaml
container_images:
  - image_id: open_webui_main
    name: Open WebUI main image
    registry: ghcr.io
    repository: open-webui/open-webui
    tag: main
    auth: none
```

## Worker handler design

Create `automation-worker/worker/handlers/container_image_update_watch.py`.

Implementation notes:

- Use `httpx` or the existing worker HTTP client pattern if present.
- Query OCI Registry HTTP API endpoints with bounded timeout.
- Support public `ghcr.io` and public Docker Hub in v1.
- Prefer `HEAD` or `GET` manifest requests and capture `Docker-Content-Digest` when available.
- Tests must inject a fake registry client; do not require internet access.
- Compare the current digest to the most recent successful previous run for the same `task_id` and `image_id` if the worker/API client exposes run history. If that is not cleanly available, implement first-run baseline behavior and document the need for a future small state table.
- Return:

```python
{
    "status": "ok" | "degraded",
    "images": [...],
    "changed_count": 0,
    "failed_count": 0,
    "notify": bool,
    "message": "..."
}
```

Alert only when:

- digest changed from previous known digest,
- registry metadata check fails and notification policy treats failures as alertable,
- an approved image disappears, or
- `notify_on_first_seen` is explicitly true and this is the first baseline.

## Non-goals

- No Docker socket.
- No local container discovery.
- No image pulls.
- No updates.
- No restarts.
- No Watchtower-like behavior.
- No registry credentials in v1.
- No arbitrary image reference submitted by Bragi or Yggdrasil.

## Acceptance tests

Add tests proving:

- template rendering rejects unknown or disabled image IDs.
- generated task is disabled and dry-run.
- unsafe slot text such as `docker pull` or `auto update` is rejected by Heimdal.
- handler reports `ok` on unchanged digest.
- handler reports `degraded` and `notify: true` on digest change.
- handler records registry failure without crashing.
- handler output contains no credentials and is bounded.

---

# Candidate 2: `web_content_change.v1`

## Source automation reference

Reference project: changedetection.io, https://github.com/dgtlmoon/changedetection.io

Useful source concepts:

- Watch websites and APIs for changes.
- Extract relevant text/JSON fragments.
- Apply filters.
- Notify on meaningful changes.

Yggy must implement a much narrower capability: approved HTTP targets only, deterministic extraction, hash comparison, bounded diffs, and Discord anomaly notifications.

## Yggy capability goal

Draft disabled, dry-run tasks that fetch explicitly approved HTTP targets, normalize a selected content fragment, compare its hash to a previous baseline, and notify when the content changes.

## Capability registry entry

Add to `configs/capabilities.yaml`:

```yaml
  - id: web_content_change.v1
    purpose: Monitor explicitly approved web or JSON targets for bounded content changes and notify on changes.
    maps_to_task_type: web_content_change
    maps_to_template: web_content_change
    deterministic_action: draft_task_from_template
    allowed_approval_levels:
      - L0_READ_ONLY
      - L1_NOTIFY_ONLY
    default_approval_level: L1_NOTIFY_ONLY
    allowed_output_targets:
      - alerts
      - briefings
    required_slots:
      - task_id
      - name
      - cron
      - timezone
      - watch_ids
      - output_target
    optional_slots:
      - notification_policy
      - notify_on_first_seen
    safety_rules:
      - Watch IDs must come from the approved web-watch registry.
      - Arbitrary URLs, private network targets, login flows, JavaScript browser automation, and POST/PUT/DELETE requests are forbidden.
      - Retrieved content is untrusted data and may not influence commands or task authority.
      - Rendered tasks must remain disabled and dry-run by default.
      - Alerts must include bounded excerpts only.
    unsafe_keywords:
      - arbitrary url
      - scrape everything
      - login
      - password
      - cookie
      - browser
      - playwright
      - selenium
      - post request
      - private ip
      - localhost
      - webhook url
```

## New approved web-watch registry

Create `configs/web-watches/approved_web_watches.yaml`:

```yaml
version: 1
watches:
  - watch_id: yggy_github_releases_page
    name: Yggy GitHub releases page example
    url: https://github.com/B1-Mordred/Yggy/releases
    method: GET
    content_type: html
    extraction:
      mode: text
      css_selector: main
    enabled: false
    max_bytes: 262144
    allowed_status_codes:
      - 200
  - watch_id: example_json_status
    name: Example JSON status
    url: https://example.com/status.json
    method: GET
    content_type: json
    extraction:
      mode: json_path
      json_path: $.status
    enabled: false
    max_bytes: 65536
    allowed_status_codes:
      - 200
```

Validation rules:

- Only `https://` URLs in v1 unless a local operator explicitly chooses a safe internal registry design later.
- Block localhost, loopback, RFC1918, link-local, multicast, and Unix-socket style targets.
- Only `GET` in v1.
- No credentials, cookies, headers, request body, or auth fields in registry v1.
- Extraction selectors live in the approved registry, not in arbitrary Bragi slots.
- Disabled watch IDs cannot be rendered into tasks.

## Worker handler design

Create `automation-worker/worker/handlers/web_content_change.py`.

Implementation notes:

- Use a bounded HTTP client with timeout, max response bytes, max redirects, and content-type checks.
- Normalize whitespace for text extraction.
- For HTML, use a minimal parser already present in dependencies if available; otherwise add a small dependency only if acceptable. Avoid browser engines.
- For JSON, support a very small deterministic selector format or implement only dot-path traversal in v1. Do not add complex dynamic expression evaluators unless they are safely bounded.
- Compute `sha256` over normalized content.
- Compare to previous successful run baseline if available; otherwise first run records baseline and suppresses notification unless `notify_on_first_seen` is true.
- Return bounded fields:

```python
{
    "status": "ok" | "degraded",
    "watches": [...],
    "changed_count": 0,
    "failed_count": 0,
    "notify": bool,
    "message": "..."
}
```

Message content:

- watch ID and name
- old/new hash prefix
- status code
- small changed excerpt, maximum 1000 characters per watch
- no full page body

## Non-goals

- No arbitrary URLs in task slots.
- No JavaScript rendering.
- No Playwright/Selenium/browser automation.
- No logins, cookies, sessions, captchas, proxies, or request bodies.
- No screenshots.
- No full-page archival.
- No model-based page interpretation in v1.

## Acceptance tests

Add tests proving:

- template rendering rejects unknown, disabled, or unsafe watch IDs.
- private/localhost URLs in the registry are rejected by config validation.
- generated task is disabled and dry-run.
- handler first run suppresses alert by default.
- handler detects changed normalized content and sends a bounded anomaly message.
- handler handles HTTP failure without crashing.
- handler never stores or returns more than the configured excerpt limit.

---

# Candidate 3: `scheduled_task_watchdog.v1`

## Source automation reference

Reference project: Healthchecks, https://github.com/healthchecks/healthchecks

Useful source concepts:

- Alert when an expected heartbeat/check-in does not arrive.
- Model recurring job health as freshness of the latest successful signal.

Yggy should not expose public ping URLs in v1. Use Yggy's own task/run history instead.

## Yggy capability goal

Draft disabled, dry-run tasks that inspect the automation API's run history and notify when selected Yggy tasks have not completed successfully within an approved freshness window.

## Capability registry entry

Add to `configs/capabilities.yaml`:

```yaml
  - id: scheduled_task_watchdog.v1
    purpose: Monitor selected Yggy task run history and alert when expected successful runs are missing or stale.
    maps_to_task_type: scheduled_task_watchdog
    maps_to_template: scheduled_task_watchdog
    deterministic_action: draft_task_from_template
    allowed_approval_levels:
      - L0_READ_ONLY
      - L1_NOTIFY_ONLY
    default_approval_level: L1_NOTIFY_ONLY
    allowed_output_targets:
      - alerts
    required_slots:
      - task_id
      - name
      - cron
      - timezone
      - monitored_task_ids
      - output_target
    optional_slots:
      - max_age_minutes
      - require_success
      - notification_policy
    safety_rules:
      - The watchdog reads Yggy run metadata only.
      - It must not expose ping tokens, public callback URLs, approval nonces, or raw run logs.
      - It must not rerun, approve, enable, pause, or mutate monitored tasks.
      - Rendered tasks must remain disabled and dry-run by default.
    unsafe_keywords:
      - public ping url
      - expose token
      - rerun automatically
      - auto approve
      - approval nonce
      - raw log
      - webhook url
```

## Task configuration shape

Rendered tasks should include:

```yaml
type: scheduled_task_watchdog
monitored_tasks:
  - task_id: daily_local_ai_security_briefing
    max_age_minutes: 1560
    require_success: true
```

Validation rules:

- `monitored_task_ids` must exist in the current visible task registry/draft set if a validation hook can check that. If not, validate slug shape and let runtime report `unknown_task` safely.
- The watchdog must not monitor itself unless explicitly allowed and tested.
- `max_age_minutes` must be bounded, for example 5 to 10080.
- Do not read raw logs in Bragi/Yggdrasil-facing paths.

## Worker handler design

Create `automation-worker/worker/handlers/scheduled_task_watchdog.py`.

Implementation notes:

- Use `AutomationApiClient.list_runs(task_id=..., limit=...)` or the closest existing method.
- For each monitored task, find the latest completed run.
- If `require_success: true`, ignore failed/skipped/degraded runs except for reporting.
- Alert when no qualifying run is found within `max_age_minutes`.
- Return:

```python
{
    "status": "ok" | "degraded",
    "monitored_tasks": [...],
    "stale_count": 0,
    "failed_count": 0,
    "notify": bool,
    "message": "..."
}
```

## Non-goals

- No external ping receiver in v1.
- No public URLs or per-job tokens.
- No automatic reruns.
- No task mutation.
- No approval handling.
- No raw log exposure.

## Acceptance tests

Add tests proving:

- recent successful run -> `ok`, no anomaly-only alert.
- stale successful run -> `degraded`, alert.
- only failed recent runs with `require_success` -> `degraded`, alert.
- unknown monitored task -> safe degraded result, not crash.
- output includes task IDs and timestamps, not raw logs or approval data.

---

# Candidate 4: `endpoint_probe.v1`

## Source automation references

Reference projects:

- Gatus, https://github.com/TwiN/gatus
- Prometheus Blackbox Exporter, https://github.com/prometheus/blackbox_exporter

Useful source concepts:

- Probe HTTP/TCP/DNS endpoints.
- Evaluate simple deterministic conditions.
- Notify on failures.

Yggy already has `server_health.v1` and `tls_certificate_expiry.v1`, so only implement this if the operator wants approved external endpoint checks that do not fit the existing local service-health registry.

## Yggy capability goal

Draft disabled, dry-run tasks that probe explicitly approved endpoints using bounded HTTP, TCP-connect, or DNS-resolution checks and notify on anomalies.

## Capability registry entry

Potential entry:

```yaml
  - id: endpoint_probe.v1
    purpose: Monitor explicitly approved external or internal endpoints with bounded HTTP, TCP-connect, or DNS-resolution probes.
    maps_to_task_type: endpoint_probe
    maps_to_template: endpoint_probe
    deterministic_action: draft_task_from_template
    allowed_approval_levels:
      - L0_READ_ONLY
      - L1_NOTIFY_ONLY
    default_approval_level: L1_NOTIFY_ONLY
    allowed_output_targets:
      - alerts
    required_slots:
      - task_id
      - name
      - cron
      - timezone
      - endpoint_ids
      - output_target
    optional_slots:
      - notification_policy
    safety_rules:
      - Endpoint IDs must come from the approved endpoint-probe registry.
      - Arbitrary hosts, URLs, IP ranges, port ranges, and network scans are forbidden.
      - V1 supports only HTTP status checks, TCP connect checks, and DNS resolve checks.
      - ICMP, traceroute, gRPC, POST bodies, authentication, and browser checks are excluded in v1.
      - Rendered tasks must remain disabled and dry-run by default.
    unsafe_keywords:
      - scan network
      - ip range
      - port range
      - arbitrary host
      - arbitrary url
      - icmp
      - ping sweep
      - traceroute
      - grpc
      - post request
      - password
      - cookie
```

## Registry

Create `configs/endpoint-probes/endpoints.yaml`:

```yaml
version: 1
endpoints:
  - endpoint_id: yggy_ops_http
    name: Yggy ops dashboard HTTP health
    probe_type: http_status
    url: http://automation-api:8088/health
    expected_status: 200
    enabled: true
    timeout_seconds: 5
  - endpoint_id: example_dns
    name: Example DNS resolution
    probe_type: dns_resolve
    hostname: example.com
    enabled: false
    timeout_seconds: 5
```

Validation rules:

- HTTP endpoint URLs must be exact registry values.
- TCP endpoints must specify one host and one port, never ranges.
- DNS endpoints must specify one hostname, never wildcard zones.
- Do not support ICMP in v1 because it can require elevated privileges.
- If private-network blocking is desired, enforce it at registry validation; do not allow users to bypass it through task slots.

## Worker handler design

Create `automation-worker/worker/handlers/endpoint_probe.py`.

Implementation notes:

- HTTP: bounded GET, no request body, no custom headers in v1.
- TCP: `socket.create_connection((host, port), timeout=...)` only.
- DNS: `socket.getaddrinfo` with timeout if available; otherwise keep DNS out of v1.
- Return only status, latency, and error class/message truncated to 200 characters.
- Reuse notification classification already present in worker main.

## Non-goals

- No scan/discovery.
- No ICMP/ping sweep.
- No gRPC in v1.
- No POST/PUT/DELETE checks.
- No credentials, cookies, client certificates, or custom secrets.
- No TLS renewal or proxy modification; TLS expiry already has a dedicated capability.

## Acceptance tests

Add tests proving:

- unknown endpoint IDs are rejected.
- generated tasks are disabled and dry-run.
- HTTP/TCP checks are mockable and bounded.
- failures produce `degraded` and safe truncated errors.
- ICMP/range/arbitrary host requests are rejected by Heimdal.

---

# Candidate 5: `storage_usage.v1`

## Source automation reference

Reference project: Glances, https://github.com/nicolargo/glances

Useful source concepts:

- Monitor CPU, memory, disk, filesystem, network, and system state.
- Expose system status through a bounded API.

Yggy already seeds `storage_usage.v1` as a capability gap. Implement a narrow storage-only version instead of a broad host-monitoring clone.

## Yggy capability goal

Draft disabled, dry-run tasks that check approved storage volume IDs and notify when usage crosses warning or critical thresholds.

## Compatibility warning

A worker container usually sees the container filesystem, not the host filesystem. Do not pretend to monitor host disks unless the deployment provides one of these explicit, reviewed inputs:

1. a narrow internal metrics-exporter endpoint with approved volume IDs; or
2. explicit read-only bind mounts for approved paths, validated by registry ID.

Prefer an exporter-backed design if the existing metrics exporter can be extended safely.

## Capability registry entry

Potential entry:

```yaml
  - id: storage_usage.v1
    purpose: Monitor approved storage volume IDs for usage thresholds and notify on anomalies.
    maps_to_task_type: storage_usage
    maps_to_template: storage_usage
    deterministic_action: draft_task_from_template
    allowed_approval_levels:
      - L0_READ_ONLY
      - L1_NOTIFY_ONLY
    default_approval_level: L1_NOTIFY_ONLY
    allowed_output_targets:
      - alerts
    required_slots:
      - task_id
      - name
      - cron
      - timezone
      - volume_ids
      - output_target
    optional_slots:
      - warning_threshold_percent
      - critical_threshold_percent
      - notification_policy
    safety_rules:
      - Volume IDs must come from the approved storage registry.
      - The worker may read usage metrics only from approved exporter endpoints or approved read-only mounts.
      - Arbitrary paths, recursive directory scans, file listing, shell commands, and filesystem writes are forbidden.
      - Rendered tasks must remain disabled and dry-run by default.
    unsafe_keywords:
      - arbitrary path
      - scan filesystem
      - list files
      - delete files
      - move files
      - du
      - df
      - shell
      - docker
      - mount
      - unmount
```

## Registry

Create `configs/storage/volumes.yaml` only after deciding the data source:

Exporter-backed example:

```yaml
version: 1
volumes:
  - volume_id: yggy_project_backups
    name: Yggy project backups
    source_type: metrics_exporter
    metrics_url: http://metrics-exporter:8090/metrics/storage/yggy_project_backups
    enabled: true
    warning_threshold_percent: 80
    critical_threshold_percent: 90
```

Read-only mount example, only if the Docker deployment is updated deliberately:

```yaml
version: 1
volumes:
  - volume_id: yggy_backups_ro
    name: Yggy backups read-only mount
    source_type: statvfs
    mount_path: /app/backups
    enabled: true
    warning_threshold_percent: 80
    critical_threshold_percent: 90
```

Validation rules:

- Reject arbitrary paths from task slots.
- Reject `..`, symlinks, glob patterns, and recursive scanning concepts.
- Use `os.statvfs` only on registry paths if using mount-backed v1.
- Do not include filenames or directory listings in run logs.

## Worker handler design

Create `automation-worker/worker/handlers/storage_usage.py`.

Implementation notes:

- For exporter-backed mode, fetch compact JSON from the approved internal exporter URL.
- For mount-backed mode, call `os.statvfs` only on approved registry paths.
- Compute percent used and classify as `ok`, `warning`, or `critical`.
- Return only volume ID, percent used, total/free bytes rounded or exact, threshold, and status.

## Non-goals

- No file listing.
- No recursive directory sizing.
- No cleanup automation.
- No delete/move/compress/archive behavior.
- No shell `df` or `du`.
- No Docker volume inspection.
- No host mount changes.

## Acceptance tests

Add tests proving:

- unknown volume IDs are rejected.
- arbitrary path requests become unsupported or unsafe.
- generated tasks are disabled and dry-run.
- handler correctly classifies below warning, warning, critical, and failure cases.
- run logs do not include filenames.

---

# Deferred candidate: `smart_drive_health.v1`

## Source automation reference

Reference project: Scrutiny, https://github.com/AnalogJ/scrutiny

Scrutiny is useful for SMART drive monitoring, but its normal deployment patterns involve host disk device access and smartctl/smartd style collection. That does not fit Yggy's current worker boundary.

Do not implement `smart_drive_health.v1` directly in the Yggy worker.

Future safe design, if desired:

- An operator-managed host exporter reads SMART data outside model-facing Yggy containers.
- The exporter exposes sanitized read-only JSON for approved drive IDs.
- Yggy worker checks only that exporter endpoint through an approved registry.
- No drive discovery, `smartctl`, shell execution, Docker device mounts, privileged containers, or raw serial numbers in model-facing logs.

Until that exporter exists, Bragi should route SMART-drive requests to a non-executable capability proposal or capability gap.

---

# Deferred candidate: `http_security_headers.v1`

## Source automation reference

Reference project: Mozilla HTTP Observatory, https://github.com/mozilla/http-observatory

The repository is archived/deprecated, but the concept is useful: deterministic checks of security headers on approved HTTPS endpoints.

Do not copy the full scanner. A future Yggy-compatible v1 should be a small approved-endpoint header checker that verifies only explicit policies such as:

- HTTPS reachable.
- `Strict-Transport-Security` present when required.
- `Content-Security-Policy` present when required.
- `X-Content-Type-Options: nosniff` present when required.
- Redirect policy matches the approved registry.

Non-goals:

- no arbitrary scan targets;
- no grading engine in v1;
- no browser automation;
- no crawling;
- no remediation or reverse-proxy edits.

---

# Suggested implementation order

1. `container_image_update_watch.v1`
2. `web_content_change.v1`
3. `scheduled_task_watchdog.v1`
4. `endpoint_probe.v1`, only if it clearly adds value beyond `server_health.v1` and `tls_certificate_expiry.v1`
5. `storage_usage.v1`, only after the operator chooses exporter-backed or read-only-mount-backed measurement

Do not implement deferred candidates until their boundary issues are resolved.

## Codex prompt template

Use this template when starting one capability implementation:

```text
You are implementing a single Yggy capability on branch dev.

Capability: <capability_id>

Read first:
- README.md
- SECURITY.md
- docs/BRAGI_HEIMDAL_INTEGRATION.md
- docs/CAPABILITY_IMPLEMENTATION_AGENT.md
- docs/TASK_TEMPLATES.md
- docs/TASK_SCHEMA.md
- configs/capabilities.yaml
- configs/policies.yaml
- automation-api/app/services/capability_gateway.py
- automation-api/app/services/task_template_service.py
- scripts/task_template_lib.py
- automation-worker/worker/main.py

Implement only this capability. Do not widen model-facing authority. Do not add shell execution, Docker socket access, arbitrary URLs, secrets, credentials, deployment authority, approval authority, or automatic remediation.

Follow the capability instructions in docs/CODEX_AUTOMATION_CAPABILITY_RESEARCH.md.

Required output:
1. capability registry entry;
2. disabled dry-run task template;
3. approved target registry if needed;
4. API validation and Heimdal support;
5. task-template rendering support;
6. worker handler with mockable IO;
7. dispatch wiring;
8. focused tests;
9. documentation updates;
10. validation command results.

Before finishing, run:
python scripts/validate_configs.py
pytest automation-api/tests/test_capability_gateway.py automation-api/tests/test_task_templates.py automation-api/tests/test_task_validation.py
pytest automation-worker/tests yggdrasil/tests bragi/tests

If any command cannot run, state the exact reason and provide the smallest remaining fix.
```

## Completion checklist

A capability implementation is not complete until all of these are true:

- [ ] `configs/capabilities.yaml` contains the new capability without deleting or weakening existing entries.
- [ ] The task template renders `enabled: false` and `runtime.dry_run: true`.
- [ ] The template renderer rejects unknown target IDs.
- [ ] Policy validation rejects unsafe flags and arbitrary targets.
- [ ] Heimdal rejects unsafe or unsupported natural-language requests before Yggdrasil.
- [ ] The worker handler is deterministic and uses mockable bounded IO.
- [ ] The worker handler has no shell, Docker, arbitrary filesystem, credential, or remediation code.
- [ ] Output is bounded and does not contain secrets, credentials, raw logs, full pages, or large payloads.
- [ ] Discord notification behavior respects anomaly-only output and task notification preferences.
- [ ] Focused API, worker, Yggdrasil, and Bragi tests pass or are updated appropriately.
- [ ] `python scripts/validate_configs.py` passes.
- [ ] Documentation explains the registry, safety boundary, render command, and non-goals.
