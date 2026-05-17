# Task Templates

Task templates are non-secret scaffolds for creating disabled task YAML with known-safe defaults. They are intended for day-to-day drafting from Open WebUI/Yggdrasil or from a local shell without hand-copying large task configs.

Templates are not an approval authority. A rendered task must still pass the normal Pydantic schema and policy validator, then be imported or drafted through the automation API, reviewed, approved, and enabled through the existing control-plane workflow.

## Catalog

Templates live in:

```text
configs/task_templates/
```

Current templates:

```text
topic_digest
server_health
backup_verification
n8n_webhook
```

Each template declares:

```yaml
id: topic_digest
name: Topic Digest
description: Draft a bounded digest from approved source IDs.
task_type: topic_digest
default_approval_level: L1_NOTIFY_ONLY
allowed_output_targets:
  - briefings
required_fields:
  - id
  - name
optional_fields:
  - cron
  - timezone
  - output_target
safety_notes:
  - Sources are rendered only from enabled approved-source entries.
example_prompts:
  - Draft a weekday 08:00 local AI security briefing to Discord.
defaults:
  ...
```

## Safety Rules

The renderer forces these properties:

```text
enabled: false
runtime.dry_run: true
policy.allow_shell: false
policy.allow_docker_socket: false
policy.allow_external_side_effects: false
policy.allow_filesystem_write: false
```

It also rejects:

```text
unknown templates
missing required values
output targets outside the template allowlist
topic digest source IDs not enabled in configs/sources/approved_sources.yaml
configs that fail the normal task policy validator
```

For topic digests, the template stores only source IDs. Rendering expands those IDs from:

```text
configs/sources/approved_sources.yaml
```

This keeps broad web-query drafting out of routine tasks and preserves the approved-source audit trail.

## List Templates

```bash
python scripts/list_task_templates.py
python scripts/list_task_templates.py --json
```

## Render A Task

Render a disabled dry-run weekday briefing:

```bash
python scripts/render_task_template.py topic_digest \
  --id draft_local_ai_weekday_briefing \
  --name "Draft Local AI Weekday Briefing" \
  --cron "0 8 * * 1-5" \
  --output-target briefings \
  --source-id open_webui_releases \
  --source-id ollama_releases \
  --source-id n8n_releases \
  --source-id docker_blog \
  --out configs/tasks/draft_local_ai_weekday_briefing.yaml
```

Validate the rendered YAML:

```bash
python scripts/validate_configs.py
```

Then import it as a disabled draft:

```bash
python scripts/import_task_drafts.py --task-id draft_local_ai_weekday_briefing --request-approval --print-nonces
```

The approval nonce is local operator material. Do not paste it into Open WebUI or chat unless you are intentionally using a trusted local-only approval flow.

## Yggdrasil Usage

Yggdrasil can tell the user what templates are available and what each template is for. It should still draft task YAML through the automation API and normal approval workflow rather than treating a template as permission to enable or run a new recurring task.

Useful prompts:

```text
Yggdrasil, list task templates.
Yggdrasil, show available automation templates.
Yggdrasil, draft a weekday 08:00 local AI security briefing to Discord, keep it disabled, and show the approval requirement.
```

## Adding A Template

1. Add a YAML file under `configs/task_templates/`.
2. Make defaults disabled/dry-run and keep forbidden capability flags false.
3. Use only whitelisted output targets.
4. For topic digests, use approved source IDs.
5. Run:

```bash
python scripts/validate_configs.py
pytest automation-api/tests/test_task_templates.py
```

Template changes are Git-reviewed config changes. They do not mutate live MySQL state until an operator renders/imports a task and approves it.
