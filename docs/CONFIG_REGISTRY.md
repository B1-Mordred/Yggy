# Configuration Registry

The YAML files under `configs/` are the reviewable configuration registry. The
live automation state is stored in MySQL and exposed through the automation API.
These scripts reconcile the two without making Open WebUI, Hermes, or the model
the source of approval authority.

## Rules

- YAML is declarative and non-secret.
- Importing YAML never enables a task directly.
- Existing live tasks are not overwritten unless `--update-existing` is passed.
- Approval nonces are local operator material. Do not paste them into chat,
  Open WebUI Knowledge, Git, logs, or Markdown.
- API responses are already redacted by the automation API, and exports go to
  `exports/`, which is ignored by Git.

## Validate Local YAML

```bash
python scripts/validate_configs.py
```

This uses the same Pydantic schemas and policy checks as the API.

## Export Live Configs

```bash
python scripts/export_live_configs.py --clean
```

Default output:

```text
exports/live/
├── manifest.json
├── tasks/
└── topics/
```

This is a generated snapshot for review and diffing. It does not overwrite
`configs/`.

## Diff Git YAML Against Live API State

```bash
python scripts/diff_registry.py
```

The command exits with:

- `0`: no drift
- `1`: drift detected
- `2`: validation/API error

For inspection without failing a shell pipeline:

```bash
python scripts/diff_registry.py --no-fail-on-drift
```

Machine-readable output:

```bash
python scripts/diff_registry.py --json --no-fail-on-drift
```

The diff calls out important fields such as:

- `enabled`
- `trigger.cron`
- `trigger.timezone`
- `output.channel`
- `output.target`
- `policy.approval_level`
- forbidden capability flags
- `runtime.dry_run`

## Import YAML As Disabled Drafts

Create missing live tasks from YAML:

```bash
python scripts/import_task_drafts.py --task-id yggy_backup_verification
```

Existing tasks are skipped by default. To update an existing task as a disabled
draft:

```bash
python scripts/import_task_drafts.py --task-id yggy_backup_verification --update-existing
```

To request approval for an existing update, explicitly print the local nonce:

```bash
python scripts/import_task_drafts.py \
  --task-id yggy_backup_verification \
  --update-existing \
  --request-approval \
  --print-nonces
```

Use the nonce only in the local approval CLI/UI. Do not paste it into chat.

Dry-run validation:

```bash
python scripts/import_task_drafts.py --dry-run
```

## Typical Operator Workflow

```bash
python scripts/validate_configs.py
python scripts/export_live_configs.py --clean
python scripts/diff_registry.py --no-fail-on-drift
python scripts/import_task_drafts.py --task-id some_task --dry-run
python scripts/import_task_drafts.py --task-id some_task
```

Then review and approve through the local `/ops` UI or the local approval CLI.

## Drift Interpretation

`missing_live` means YAML exists but no corresponding live task/topic exists.
This is expected for examples or tasks staged for future import.

`missing_yaml` means live state exists without a matching YAML file. This should
usually be exported and reviewed so Git has the same operational intent.

`field_changed` means the same resource exists in both places but the normalized
declarative config differs. Treat changes to schedule, output target, approval
level, forbidden capability flags, or dry-run mode as higher priority review
items.
