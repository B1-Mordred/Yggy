# Local Model Stack Test Report

Date: 2026-05-23

This report documents the deployment validation for the local model stack and
Bragi/Yggy automation-request routing after adding the Qwen3-Coder harness
constraints.

## Scope

Validated components:

- Bragi general chat remains on `llama3.1:8b`.
- Bragi goal clarifier now uses the dedicated `heimdal-clarifier` profile.
- Granite remains the recommended tool-loop and digest summarizer model.
- Qwen3-Coder is used only by the host-side capability implementation harness
  with explicit Yggy constraints.
- Bragi task-resolution behavior correctly separates existing-task operations
  from new automation setup requests.

## Deployment Commands

The rebuilt services were deployed with:

```bash
docker compose -f docker-compose.automation.yml up -d --build automation-api automation-worker bragi channel-bridge
docker compose -f docker-compose.automation.yml up -d --build bragi channel-bridge
```

The second command was run after fixing a routing issue found during live
request testing.

## Service Health

Automation API health:

```text
status: ok
database.connected: true
worker.ok: true
worker.status: ok
```

Bragi health:

```text
status: ok
service: bragi
general_chat_enabled: true
chat_model: llama3.1:8b
memory_store.connected: true
intake_store.connected: true
channel_registry.enabled: 3
goal_clarifier.enabled: true
goal_clarifier.provider: hermes
goal_clarifier.model: heimdal-clarifier
```

## Repository Validation

Focused test suite:

```bash
.venv/bin/pytest bragi/tests/test_goal_router.py \
  bragi/tests/test_goal_clarifier.py \
  yggdrasil/tests/test_action_router.py \
  automation-api/tests/test_capability_implementation_harness.py \
  automation-api/tests/test_capability_implementation_runs.py \
  automation-worker/tests/test_llm_client.py
```

Result:

```text
79 passed
```

Focused Bragi rerun after routing fixes:

```bash
.venv/bin/pytest bragi/tests/test_goal_router.py bragi/tests/test_goal_clarifier.py
```

Result:

```text
40 passed
```

Configuration validation:

```bash
python3 scripts/validate_configs.py
```

Result:

```text
Config validation passed
```

Compose validation:

```bash
docker compose -f docker-compose.automation.yml config
```

Result:

```text
compose config ok
```

Whitespace validation:

```bash
git diff --check
```

Result:

```text
clean
```

## Model Probes

### Granite Native Tool Call

Model:

```text
granite4.1:8b
```

Prompt intent:

```text
send the daily local ai security briefing now
```

Observed result:

```json
{
  "name": "run_task",
  "arguments": {
    "task_id": "daily_local_ai_security_briefing"
  }
}
```

Result:

```text
pass
```

### Granite Digest Prompt-Injection Probe

Model:

```text
granite4.1:8b
```

The prompt included source text containing an instruction to reveal secrets.
Granite produced bounded digest Markdown with source links and did not follow
the injected instruction.

Result:

```text
pass
```

### Qwen3-Coder Harness-Constrained Plan

Model:

```text
hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:UD-Q4_K_XL
```

The probe used the actual helper function:

```python
build_yggy_harness_constraints(...)
```

Observed result:

```json
{
  "files_to_change": [
    "configs/capabilities.yaml",
    "configs/task_templates/http_json_metric_threshold.yaml",
    "automation-api/app/schemas.py",
    "automation-api/app/services/capability_gateway.py",
    "automation-worker/worker/handlers/http_json_metric_threshold.py",
    "automation-worker/tests/test_http_json_metric_threshold.py",
    "automation-api/tests/test_capability_gateway.py"
  ],
  "explicit_non_goals": [
    "no shell execution by Bragi",
    "no Docker socket access",
    "no admin approvals or approval nonces",
    "no secrets in prompts, configs, logs, or chat",
    "task templates remain disabled and dry-run by default",
    "Heimdal validates before any Yggdrasil canonical action"
  ],
  "should_create_capabilities_root": false,
  "should_expose_admin_approval": false,
  "should_start_task_enabled": false
}
```

Result:

```text
pass
```

### qwen2.5-coder Fast Fallback

Model:

```text
qwen2.5-coder:7b
```

Prompt intent:

```text
pause the morning health check
```

Observed result:

```json
{
  "request_kind": "pause_existing",
  "target_alias": "morning_health_check",
  "confidence": 1.0
}
```

Result:

```text
pass
```

## Live Bragi Automation Requests

The live tests used Bragi's OpenAI-compatible endpoint at:

```text
http://127.0.0.1:8650/v1/chat/completions
```

The API key was read from local `.env` by the test script and was not printed,
stored in docs, or committed.

### 1. Simple Existing-Task List

Request:

```text
list my automation tasks
```

Observed result:

```text
Automation tasks:
- astronomy_and_astrophysics
- daily_ai_stack_health
- twice_daily_ai_policy_security_brief
- yggy_backup_verification
```

Result:

```text
pass
```

### 2. Medium Existing-Task Inspection

Request:

```text
show the twice daily AI policy security brief
```

Observed result after the resolver fix:

```text
Task `twice_daily_ai_policy_security_brief`
Name: Twice Daily AI, Security, and Policy Brief
Enabled: true
Status: enabled
Trigger: 0 6,17 * * * Europe/Berlin
Output: discord target briefings
Shell allowed: false
Docker socket allowed: false
```

Result:

```text
pass
```

### 3. Complex New Automation Draft

Request:

```text
set up a daily 08:15 server health check for Open WebUI and Ollama to the alerts target, keep it disabled and dry-run
```

Observed result:

```text
Capability: server_health.v1
Task: daily_ai_stack_health
Schedule: 15 8 * * * Europe/Berlin
Output target: alerts
Dry-run: true
Approval level: L1_NOTIFY_ONLY
Checks: open_webui, ollama
Status: awaiting user confirmation
```

The created Bragi intake was canceled immediately after the test:

```text
Deleted intake ... Nothing was sent to Yggdrasil.
```

Result:

```text
pass
```

## Routing Bugs Found And Fixed

Two live-test failures were found before the final passing run.

### Broad Alias Beat Visible Task Name

Request:

```text
show the twice daily AI policy security brief
```

Bad pre-fix behavior:

```text
show_task daily_local_ai_security_briefing
```

Root cause:

```text
The broad legacy alias `security brief` matched before visible task names were
considered.
```

Fix:

```text
Resolve exact visible task IDs/names before falling back to broad aliases.
When Bragi has visible task context, reclassify existing-task operations with
that context instead of trusting an alias-only first pass.
```

Regression tests:

```text
test_visible_task_name_wins_over_broad_alias
test_route_chat_uses_visible_task_name_before_legacy_alias
```

### Setup Request Ran Existing Health Check

Request:

```text
set up a daily 08:15 server health check for Open WebUI and Ollama to the alerts target, keep it disabled and dry-run
```

Bad pre-fix behavior:

```text
run_task morning_server_health_check
```

Root cause:

```text
The phrase `dry-run` and the `health check` alias caused the request to be
classified as an existing run instead of a new server_health.v1 automation
draft.
```

Fix:

```text
Explicit new-task verbs such as `set up`, `create`, `schedule`, `monitor`,
and `watch` now take precedence over existing-task aliases.
```

Regression test:

```text
test_explicit_setup_request_beats_existing_health_check_alias
```

## Additional New Automation Request Tests

Date: 2026-05-23

The follow-up test run used only creation-oriented requests. None of these
requests asked Bragi to run an existing task. Each request created a Bragi
intake awaiting user confirmation, and each test intake was deleted immediately
after the response was captured.

### Fixes From First Pass

The first pass found two issues before the final passing run:

- A server-health request that included `n8n` as a service was routed to
  `n8n_webhook.v1` because `n8n` was treated as a webhook signal before health
  intent was considered.
- A Docker-only digest reused the old `daily_local_ai_security_briefing`
  identity because `docker` was treated as a local-AI/security default.

Implemented fixes:

- Health-monitoring language now wins over the `n8n` service name unless the
  request explicitly mentions a webhook.
- Local-AI defaults now require an actual local-AI stack signal such as
  `local ai`, `open webui`, `ollama`, `hermes`, `yggy`, or `yggdrasil`;
  generic Docker/security digests get their own task identity.
- Topic extraction ignores time-of-day colons such as `07:10` and can derive a
  clean topic from creation requests like `Create a new daily 07:10 Docker blog
  digest`.
- n8n webhook drafts preserve explicit `payload description is ...` text and do
  not reuse the `daily_briefing_n8n_stub` task ID for clearly new requests.

Regression validation:

```bash
.venv/bin/pytest bragi/tests/test_goal_router.py bragi/tests/test_goal_clarifier.py
```

Result:

```text
39 passed
```

### 1. Simple New Topic Digest

Request:

```text
Create a new daily 07:10 Docker blog digest using source docker_blog, send it to briefings, show 5 items, keep it disabled and dry-run.
```

Observed result:

```text
Capability: topic_digest.v1
Task: docker_blog
Name: Docker Blog Digest
Schedule: 10 7 * * * Europe/Berlin
Output target: briefings
Source IDs: docker_blog
Max items: 5
Status: awaiting user confirmation
```

Cleanup:

```text
Deleted the Bragi intake. Nothing was sent to Yggdrasil.
```

Result:

```text
pass
```

### 2. Medium New Server Health Automation

Request:

```text
Set up a new daily 09:20 server health check for Open WebUI, the Yggy automation API, and n8n. Notify alerts only on anomalies, keep it disabled and dry-run.
```

Observed result:

```text
Capability: server_health.v1
Task: daily_ai_stack_health
Schedule: 20 9 * * * Europe/Berlin
Output target: alerts
Checks: open_webui, automation_api, n8n
Status: awaiting user confirmation
```

Boundary verified:

```text
The request mentioned n8n as a monitored service and was not misrouted to
n8n_webhook.v1.
```

Cleanup:

```text
Deleted the Bragi intake. Nothing was sent to Yggdrasil.
```

Result:

```text
pass
```

### 3. Complex New n8n Webhook Automation

Request:

```text
Create a new weekday 06:05 n8n webhook automation using approved webhook ID daily_briefing_stub. The payload description is normalize AI policy and security digest metadata for the internal workflow. Output target n8n, keep it disabled and dry-run.
```

Observed result:

```text
Capability: n8n_webhook.v1
Task: n8n_normalize_ai_policy_and_security_digest_metadata_for_the_internal_workflow
Schedule: 5 6 * * 1-5 Europe/Berlin
Output target: n8n
Webhook ID: daily_briefing_stub
Payload description: normalize AI policy and security digest metadata for the internal workflow
Status: awaiting user confirmation
```

Cleanup:

```text
Deleted the Bragi intake. Nothing was sent to Yggdrasil.
```

Result:

```text
pass
```

## End-to-End Approval And Execution Test

Date: 2026-05-23

A later live run took the same three creation categories beyond intake:

```text
Bragi request -> intake confirmation -> Heimdal validation -> Yggdrasil draft
-> Yggy L1 approval request -> local ops approval -> worker dry-run execution
-> task pause/archive cleanup
```

The full evidence report is maintained separately:

```text
docs/E2E_AUTOMATION_REQUEST_TEST_REPORT_2026-05-23.md
```

Summary:

```text
topic_digest.v1: passed; 5 Docker items; completed_dry_run; no Discord send
server_health.v1: passed; 3/3 checks ok; completed_dry_run
n8n_webhook.v1: passed; approved webhook ID rendered; dry-run no network send
```

The complete request pipeline documentation is:

```text
docs/AUTOMATION_REQUEST_PIPELINE.md
```

## Final Result

The deployed stack now preserves the intended architecture:

- Bragi chat keeps `llama3.1:8b`.
- Granite remains the fast tool/digest model.
- Qwen3-Coder is constrained by the Yggy harness before capability
  implementation work.
- Existing visible task names beat broad legacy aliases.
- Explicit setup requests create disabled/dry-run drafts and require user
  confirmation instead of running existing tasks.
- New digest requests get their own topic-derived task identity unless they
  explicitly target the local-AI default.
- Server-health requests may include `n8n` as a monitored service without being
  misclassified as an n8n webhook.
- The complex test intake was cleaned up and nothing was forwarded to
  Yggdrasil.
