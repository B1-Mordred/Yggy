# Approval Notifications

Approval notifications make pending Yggy approvals visible in Discord without
moving approval authority into Discord.

Discord is notification-only:

- it may show that an approval is pending
- it may show the task id, approval level, actions, failure mode, and redacted
  config diff summary
- it must not include the admin API key
- it must not include nonce hashes
- it does not approve, reject, enable, pause, or run tasks

Approvals still happen only through the local `/ops` UI or the local approval
CLI.

## Dry-Run

```bash
python scripts/notify_pending_approvals.py --dry-run --all
```

This calls the automation API and records a dry-run Discord send. No Discord
network request is made.

## Notify One Approval

```bash
python scripts/notify_pending_approvals.py --approval-id <approval-id>
```

This sends one live Discord message to the whitelisted `approvals` target.

## Notify All Pending Approvals

```bash
python scripts/notify_pending_approvals.py --all
```

Live sends require either `--approval-id` or `--all` so an operator has to make
an explicit selection.

## Output Target

The script only allows:

```text
approvals
```

Do not route approval notifications to `briefings`, `alerts`, a public channel,
or direct Discord webhooks outside the automation API.

## What To Do When A Notification Appears

1. Open the local operations UI:

   ```text
   https://yggy.b1.germering:8443/ops
   ```

2. Review the task, actions, failure mode, and config diff.
3. Enter the approval nonce only in the local UI or local CLI.
4. Reject approvals that are unexpected, stale, unclear, or riskier than their
   approval level suggests.

Never paste `AUTOMATION_ADMIN_API_KEY`, approval nonces, Discord tokens, webhook
URLs, or database passwords into Open WebUI, Hermes, Discord, Git, logs, or task
YAML.
