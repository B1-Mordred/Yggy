# End-to-End Automation Request Test Report

Date: 2026-05-23

This report documents a live end-to-end test of three new automation requests.
The tests intentionally exercised the full controlled path:

```text
Bragi natural request
  -> Bragi intake and user confirmation
  -> Heimdal capability validation
  -> Yggdrasil canonical task draft
  -> Yggy task draft and approval request
  -> local L1 ops approval
  -> worker dry-run execution
  -> run evidence
  -> test-task cleanup
```

No existing automation was used as the test target. Each test used a unique
test task ID. The generated L1 approval requests were approved through the local
ops/admin path for this test run. No admin key, approval nonce, webhook URL, or
secret is included in this report.

## Environment

Live services at test time:

```text
automation-api: healthy
automation-worker: up
bragi: healthy
channel-bridge: up
```

Bragi health relevant fields:

```text
general_chat_enabled: true
chat_model: llama3.1:8b
goal_clarifier.enabled: true
goal_clarifier.model: bragi-clarifier
memory_store.connected: true
intake_store.connected: true
```

## Code Fix Required Before End-to-End Test

The previous creation-only test showed that the builders needed explicit test
task IDs to avoid collisions with real existing automations. This run added and
validated explicit task ID/name extraction for creation requests:

```text
task id <slug>
named "<display name>"
```

Additional validation before the live run:

```bash
.venv/bin/pytest bragi/tests/test_goal_router.py bragi/tests/test_goal_clarifier.py
```

Result:

```text
40 passed
```

## Test 1: Simple Topic Digest

Request sent to Bragi:

```text
Create a new daily 07:10 Docker digest with task id codex_e2e_docker_digest_20260523_1504 named "Codex E2E Docker Digest" using source docker_blog, send it to briefings, show 5 items, keep it disabled and dry-run.
```

Bragi created an intake:

```text
Intake: bragi_intake_20260523_130438_ee3319e9
Capability: topic_digest.v1
Task: codex_e2e_docker_digest_20260523_1504
Schedule: 10 7 * * * Europe/Berlin
Output target: briefings
Sources: docker_blog
Max items: 5
Dry-run: true
```

User confirmation was sent through Bragi:

```text
confirm intake bragi_intake_20260523_130438_ee3319e9
```

Yggdrasil created the task draft from template:

```text
Template: topic_digest
Task status after draft: pending_approval
Approval level: L1_NOTIFY_ONLY
Approval request: created
```

The L1 approval was accepted through the local ops approval endpoint. After
approval:

```text
Task status: enabled
Task enabled: true
Task type: topic_digest
```

The task was run through the local ops dry-run endpoint.

Run evidence:

```text
Run ID: fe052796-a82b-4d16-b645-2b8fb5e47ac1
Run status: completed_dry_run
Result status: dry_run
Item count: 5
Configured source count: 1
Processed source count: 1
Successful source count: 1
Source errors: 0
Summary mode: llm
Discord delivery dry-run: true
Discord sent: false
Notification decision: success
```

Evidence excerpt:

```text
The Docker digest produced five source-backed items from docker_blog and built
a dry-run Discord message preview. No Discord network send occurred.
```

Cleanup:

```text
Task paused: yes
Task archived: yes
Final task status: archived
```

Result:

```text
pass
```

## Test 2: Medium Server Health Automation

Request sent to Bragi:

```text
Set up a new daily 09:20 server health check with task id codex_e2e_stack_health_20260523_1504 named "Codex E2E Stack Health" for Open WebUI, the Yggy automation API, and n8n. Notify alerts only on anomalies, keep it disabled and dry-run.
```

Bragi created an intake:

```text
Intake: bragi_intake_20260523_130510_1f628b9e
Capability: server_health.v1
Task: codex_e2e_stack_health_20260523_1504
Schedule: 20 9 * * * Europe/Berlin
Output target: alerts
Checks: open_webui, automation_api, n8n
Dry-run: true
```

User confirmation was sent through Bragi:

```text
confirm intake bragi_intake_20260523_130510_1f628b9e
```

Yggdrasil created the task draft from template:

```text
Template: server_health
Task status after draft: pending_approval
Approval level: L1_NOTIFY_ONLY
Approval request: created
```

The L1 approval was accepted through the local ops approval endpoint. After
approval:

```text
Task status: enabled
Task enabled: true
Task type: server_health
```

The task was run through the local ops dry-run endpoint.

Run evidence:

```text
Run ID: 807e0ad0-7a8a-4778-96fb-312f407dc47e
Run status: completed_dry_run
Result status: ok
Checks executed: 3
Checks OK: 3
Checks failed: 0
open_webui: HTTP 200, ok
automation_api: HTTP 200, ok
n8n: HTTP 200, ok
```

Evidence excerpt:

```text
The worker checked Open WebUI, the automation API, and n8n. All configured
checks returned HTTP 200. Because output is anomaly-only and no anomalies were
detected, Discord alerting was suppressed.
```

Cleanup:

```text
Task paused: yes
Task archived: yes
Final task status: archived
```

Result:

```text
pass
```

## Test 3: Complex n8n Webhook Automation

Request sent to Bragi:

```text
Create a new weekday 06:05 n8n webhook automation with task id codex_e2e_n8n_payload_20260523_1504 named "Codex E2E n8n Payload" using approved webhook ID daily_briefing_stub. The payload description is normalize AI policy and security digest metadata for the internal workflow. Output target n8n, keep it disabled and dry-run.
```

Bragi created an intake:

```text
Intake: bragi_intake_20260523_130511_b83a162d
Capability: n8n_webhook.v1
Task: codex_e2e_n8n_payload_20260523_1504
Schedule: 5 6 * * 1-5 Europe/Berlin
Output target: n8n
Webhook ID: daily_briefing_stub
Payload description: normalize AI policy and security digest metadata for the internal workflow
Dry-run: true
```

User confirmation was sent through Bragi:

```text
confirm intake bragi_intake_20260523_130511_b83a162d
```

Yggdrasil created the task draft from template:

```text
Template: n8n_webhook
Task status after draft: pending_approval
Approval level: L1_NOTIFY_ONLY
Approval request: created
```

The L1 approval was accepted through the local ops approval endpoint. After
approval:

```text
Task status: enabled
Task enabled: true
Task type: n8n_webhook
```

The task was run through the local ops dry-run endpoint.

Run evidence:

```text
Run ID: da580301-a84a-47b9-a093-e2fc30c6be2a
Run status: completed_dry_run
Result status: dry_run
Webhook ID: daily_briefing_stub
Payload keys: description
Network request sent: no
```

Evidence excerpt:

```text
n8n webhook daily_briefing_stub dry-run; no network request sent.
```

Cleanup:

```text
Task paused: yes
Task archived: yes
Final task status: archived
```

Result:

```text
pass
```

## Final Assessment

All three new automation requests completed the full intended path:

```text
natural request -> Bragi intake -> user confirmation -> Heimdal validation
-> Yggdrasil deterministic draft -> Yggy approval request -> local L1 approval
-> enabled task -> worker dry-run execution -> run evidence -> test cleanup
```

Evidence summary:

```text
topic_digest.v1: passed, 5 Docker items, dry-run Discord preview, no send
server_health.v1: passed, 3/3 checks ok, anomaly-only alert suppressed
n8n_webhook.v1: passed, approved webhook ID rendered, dry-run no network send
```

Safety summary:

```text
No shell access granted to Bragi.
No Docker socket access granted to Bragi.
No admin key exposed to Bragi, Hermes, Open WebUI, Discord, docs, or logs.
No approval nonce included in the model-facing path or this report.
All generated tasks started disabled and dry-run.
All approvals were L1_NOTIFY_ONLY.
All test tasks were paused and archived after evidence collection.
```
