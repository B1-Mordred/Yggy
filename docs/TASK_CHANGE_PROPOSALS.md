# Task Change Proposals

Task change proposals are the safe path for modifying existing automations,
especially enabled recurring tasks. The model-facing tool key may propose a
validated change, but it cannot approve, apply, or reject that change.

## Flow

```text
Yggdrasil/Open WebUI
  -> POST /tasks/{task_id}/propose-change
  -> API validates full proposed TaskConfig
  -> API stores redacted before/after config, diff, risk, and nonce
  -> Admin approves with nonce
  -> Admin applies the approved proposal
  -> API records config version and audit event
```

The proposal stores a hash of the base task config. Apply fails if the live task
changed after the proposal was created.

## API Endpoints

Model-facing allowed:

```text
POST /tasks/{task_id}/propose-change
GET /task-change-proposals
GET /task-change-proposals/{proposal_id}
```

Admin-only:

```text
POST /task-change-proposals/{proposal_id}/approve
POST /task-change-proposals/{proposal_id}/reject
POST /task-change-proposals/{proposal_id}/apply
```

The approve endpoint requires the nonce returned when the proposal was created.
The nonce is local operator material. Do not paste admin keys or long-lived
secrets into chat, Open WebUI Knowledge, task YAML, or logs.

## Local CLI

Create a proposal from a full proposed task YAML:

```bash
python scripts/propose_task_change.py \
  --task-id daily_local_ai_security_briefing \
  --proposed-config /path/to/proposed-task.yaml \
  --summary "Move the weekday briefing to 07:30"
```

Approve and apply:

```bash
python scripts/approve_task_change.py \
  --proposal-id <proposal-id> \
  --nonce <nonce> \
  --apply
```

Reject:

```bash
python scripts/reject_task_change.py \
  --proposal-id <proposal-id> \
  --reason "Not needed"
```

## Yggdrasil Usage

Current supported Yggdrasil shortcut:

```text
Yggdrasil, change the daily briefing schedule to 07:30 weekdays.
Yggdrasil, show pending task change proposals.
Yggdrasil, show task change proposal <proposal-id>.
```

Yggdrasil creates the proposal and shows the nonce, but it cannot approve or
apply it. Approval and application stay on the local admin path.

## Risk Signals

The API marks risk categories when these paths change:

```text
enabled
trigger.cron
trigger.timezone
sources
checks
output.channel
output.target
runtime.dry_run
policy.approval_level
policy.allow_external_side_effects
policy.allow_filesystem_write
policy.allow_shell
policy.allow_docker_socket
n8n
backup.backup_root
```

L4 proposals are manual-only and cannot be applied through the autonomous
proposal endpoint.
