# Local Model Stack Test Report

Date: 2026-05-23

This report documents the deployment validation for the local model stack and
Bragi/Yggy automation-request routing after adding the Qwen3-Coder harness
constraints.

## Scope

Validated components:

- Bragi general chat remains on `llama3.1:8b`.
- Bragi goal clarifier remains the dedicated `bragi-clarifier` profile.
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
goal_clarifier.model: bragi-clarifier
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
75 passed
```

Focused Bragi rerun after routing fixes:

```bash
.venv/bin/pytest bragi/tests/test_goal_router.py bragi/tests/test_goal_clarifier.py
```

Result:

```text
36 passed
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

## Final Result

The deployed stack now preserves the intended architecture:

- Bragi chat keeps `llama3.1:8b`.
- Granite remains the fast tool/digest model.
- Qwen3-Coder is constrained by the Yggy harness before capability
  implementation work.
- Existing visible task names beat broad legacy aliases.
- Explicit setup requests create disabled/dry-run drafts and require user
  confirmation instead of running existing tasks.
- The complex test intake was cleaned up and nothing was forwarded to
  Yggdrasil.
