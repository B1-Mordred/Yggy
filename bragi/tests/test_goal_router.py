from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bragi"))

from bragi import intake_store  # noqa: E402
from bragi import main as bragi  # noqa: E402
from bragi.goal_router import AutomationRequestKind, classify_automation_request, resolve_task_reference  # noqa: E402


@pytest.fixture(autouse=True)
def reset_bragi_intakes():
    intake_store.reset_intake_store_for_tests()


def gateway_response_for(payload: dict) -> dict:
    missing = []
    if payload["capability_id"] == "topic_digest.v1" and not payload["slots"].get("source_ids"):
        missing.append("source_ids")
    if payload["capability_id"] == "server_health.v1" and not payload["slots"].get("check_ids"):
        missing.append("check_ids")
    if payload["capability_id"] == "topic_digest.modify_subjects.v1" and not any(
        payload["slots"].get(slot)
        for slot in ("add_source_ids", "remove_source_ids", "add_include", "remove_include")
    ):
        missing.append("subject_change")
    if not payload.get("user_confirmation_obtained"):
        missing.append("user_confirmation")
    if missing:
        return {
            "outcome": "ASK_CLARIFICATION",
            "capability_id": payload["capability_id"],
            "message": "missing",
            "missing_slots": missing,
            "confirmation_summary": {
                "capability_id": payload["capability_id"],
                "task_id": payload["slots"].get("task_id"),
                "name": payload["slots"].get("name"),
                "schedule": {"cron": payload["slots"].get("cron"), "timezone": payload["slots"].get("timezone")},
                "checks": payload["slots"].get("check_ids", []),
                "sources": payload["slots"].get("source_ids", []),
                "change_type": "topic_digest_subjects" if payload["capability_id"] == "topic_digest.modify_subjects.v1" else None,
                "add_source_ids": payload["slots"].get("add_source_ids", []),
                "add_include": payload["slots"].get("add_include", []),
                "output_target": payload["slots"].get("output_target"),
                "dry_run": True,
                "approval_level": "L1_NOTIFY_ONLY",
                "worst_case_failure_mode": "A noisy alert could be sent.",
                "rollback_disable_method": "Pause through /ops.",
            },
        }
    return {
        "outcome": "ACCEPT",
        "capability_id": payload["capability_id"],
        "message": "accepted",
        "confirmation_summary": {},
        "yggdrasil_request": {"action": "draft_task_from_template"},
    }


def source_registry_response(method: str, path: str, payload=None):
    if method == "GET" and path == "/sources":
        return {
            "data": [
                {
                    "id": "cisa_news_events",
                    "name": "CISA News & Events",
                    "type": "http",
                    "enabled": True,
                    "categories": ["cybersecurity"],
                    "trust_level": "ai_safe_a_open",
                    "ai_safe_fit": "A - high-fit/open",
                    "ingestion_mode": "http_summary",
                    "description": "Official U.S. cybersecurity alerts and advisories.",
                },
                {
                    "id": "nist_national_vulnerability_database",
                    "name": "NIST National Vulnerability Database",
                    "type": "http",
                    "enabled": True,
                    "categories": ["cybersecurity"],
                    "trust_level": "ai_safe_a_open",
                    "ai_safe_fit": "A - high-fit/open",
                    "ingestion_mode": "http_summary",
                    "description": "CVE vulnerability enrichment and CVSS metrics.",
                },
            ]
        }
    return gateway_response_for(payload)


def test_resolve_task_reference_uses_alias_and_visible_tasks():
    target, candidates = resolve_task_reference(
        "Pausiere den Health Check.",
        visible_tasks=[{"id": "morning_server_health_check", "name": "Morning Server Health Check"}],
        task_aliases=bragi.TASK_ALIASES,
    )

    assert target == "morning_server_health_check"
    assert candidates == ["morning_server_health_check"]


def test_resolve_task_reference_ignores_intake_id_before_selected_task_id():
    target, candidates = resolve_task_reference(
        "Intake: bragi_intake_20260519_210303_4f697b01: twice_daily_ai_policy_security_brief",
        task_aliases=bragi.TASK_ALIASES,
    )

    assert target == "twice_daily_ai_policy_security_brief"
    assert candidates == ["twice_daily_ai_policy_security_brief"]


def test_what_tasks_question_routes_to_list_existing():
    result = classify_automation_request("What automation tasks do I have?", task_aliases=bragi.TASK_ALIASES)

    assert result.request_kind == AutomationRequestKind.LIST_EXISTING
    assert result.operation == {"action": "list_tasks"}


def test_inspect_existing_by_explicit_id_and_alias():
    explicit = classify_automation_request("inspect task daily_local_ai_security_briefing", task_aliases=bragi.TASK_ALIASES)
    alias = classify_automation_request("show the daily brief", task_aliases=bragi.TASK_ALIASES)

    assert explicit.request_kind == AutomationRequestKind.INSPECT_EXISTING
    assert explicit.operation == {"action": "show_task", "task_id": "daily_local_ai_security_briefing"}
    assert alias.request_kind == AutomationRequestKind.INSPECT_EXISTING
    assert alias.operation == {"action": "show_task", "task_id": "daily_local_ai_security_briefing"}


def test_visible_task_name_wins_over_broad_alias():
    visible_tasks = [
        {"id": "astronomy_and_astrophysics", "name": "Astronomy And Astrophysics Digest", "enabled": True},
        {"id": "daily_ai_stack_health", "name": "AI Stack Health Monitor", "enabled": True},
        {
            "id": "twice_daily_ai_policy_security_brief",
            "name": "Twice Daily AI, Security, and Policy Brief",
            "enabled": True,
        },
    ]

    result = classify_automation_request(
        "show the twice daily AI policy security brief",
        visible_tasks=visible_tasks,
        task_aliases=bragi.TASK_ALIASES,
    )

    assert result.request_kind == AutomationRequestKind.INSPECT_EXISTING
    assert result.operation == {"action": "show_task", "task_id": "twice_daily_ai_policy_security_brief"}


def test_modify_existing_topic_digest_classifies_to_task_change_intent():
    result = classify_automation_request("include Ubuntu security notices in the daily brief", task_aliases=bragi.TASK_ALIASES)

    assert result.request_kind == AutomationRequestKind.MODIFY_EXISTING
    assert result.capability_id == "topic_digest.modify_subjects.v1"
    assert result.target_task_id == "daily_local_ai_security_briefing"


def test_explicit_setup_request_beats_existing_health_check_alias():
    result = classify_automation_request(
        "set up a daily 08:15 server health check for Open WebUI and Ollama to the alerts target, keep it disabled and dry-run",
        task_aliases=bragi.TASK_ALIASES,
    )

    assert result.request_kind == AutomationRequestKind.CREATE_NEW
    assert result.capability_id == "server_health.v1"


def test_new_n8n_webhook_uses_registered_capability():
    result = classify_automation_request("create an n8n webhook task for the approved daily briefing workflow", task_aliases=bragi.TASK_ALIASES)

    assert result.request_kind == AutomationRequestKind.CREATE_NEW
    assert result.capability_id == "n8n_webhook.v1"


def test_unsupported_safe_idea_becomes_capability_proposal_classification():
    result = classify_automation_request("track UPS battery status and alert me", task_aliases=bragi.TASK_ALIASES)

    assert result.request_kind == AutomationRequestKind.PROPOSE_NEW_CAPABILITY
    assert result.target_kind.value == "new_capability"


def test_device_command_word_is_not_shell_execution_but_shell_commands_remain_unsafe():
    device = classify_automation_request(
        "Send a command to switch off the lights via WiFi using the SONOFF MINIR4M after no motion.",
        task_aliases=bragi.TASK_ALIASES,
    )
    shell = classify_automation_request("run a shell command every hour", task_aliases=bragi.TASK_ALIASES)

    assert device.request_kind == AutomationRequestKind.PROPOSE_NEW_CAPABILITY
    assert device.target_kind.value == "new_capability"
    assert shell.request_kind == AutomationRequestKind.UNSAFE
    assert "shell execution is forbidden" in shell.unsafe_reasons


def test_unsupported_safe_idea_routes_to_non_executable_capability_proposal(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        assert (method, path) == ("POST", "/capability-proposals/draft")
        return {
            "id": "capability_proposal_ups_battery",
            "status": "pending",
            "suggested_capability_id": payload["suggested_capability_id"],
            "suggested_task_type": payload["suggested_task_type"],
            "likely_approval_level": payload["likely_approval_level"],
            "purpose": payload["purpose"],
        }

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "track UPS battery status and alert me"}])

    assert calls[0][0:2] == ("POST", "/capability-proposals/draft")
    assert "Capability proposal drafted" in answer
    assert "This is backlog state only" in answer


def test_confirmation_after_conversational_smart_home_sketch_drafts_capability_proposal(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        assert (method, path) == ("POST", "/capability-proposals/draft")
        return {
            "id": "capability_proposal_smart_home_lighting_absence",
            "status": "pending",
            "suggested_capability_id": payload["suggested_capability_id"],
            "suggested_task_type": payload["suggested_task_type"],
            "likely_approval_level": payload["likely_approval_level"],
            "purpose": payload["purpose"],
        }

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    assistant_sketch = """Action:

Turn off lights: Send a command to switch off the lights via WiFi using the SONOFF MINIR4M.

To make it more robust, we can add some additional conditions or checks. For example:

We could require multiple consecutive hours of no motion detection before considering you absent.
We might want to specify a grace period after detecting your absence, during which time the lights won't turn off.

How do these suggestions sound? Would you like to refine this automation further or proceed with implementing it?"""
    answer = bragi.route_chat(
        [{"role": "assistant", "content": assistant_sketch}, {"role": "user", "content": "go ahead"}],
        channel="discord",
    )

    assert "I do not have a pending canonical intent to confirm" not in answer
    assert "prior automation idea for review" in answer
    assert "Capability proposal drafted" in answer
    assert "This is backlog state only" in answer
    assert calls[0][0:2] == ("POST", "/capability-proposals/draft")
    assert calls[0][2]["suggested_capability_id"] == "smart_home_lighting_absence.v1"
    assert calls[0][2]["suggested_task_type"] == "smart_home_lighting"
    assert calls[0][2]["likely_approval_level"] == "L3_EXTERNAL_SIDE_EFFECT"


def test_confirmation_without_pending_context_gives_clear_boundary():
    answer = bragi.route_chat([{"role": "user", "content": "go ahead"}])

    assert "pending canonical intent or Bragi intake" in answer
    assert "ordinary chat" in answer


def test_arbitrary_urls_webhook_urls_and_secrets_are_unsafe():
    arbitrary = classify_automation_request("create a brief from https://example.com/feed.xml", task_aliases=bragi.TASK_ALIASES)
    webhook = classify_automation_request("trigger this webhook URL https://example.com/webhook/abc every morning", task_aliases=bragi.TASK_ALIASES)
    secret = classify_automation_request("remember this API key token abc123 for the task", task_aliases=bragi.TASK_ALIASES)

    assert arbitrary.request_kind == AutomationRequestKind.UNSAFE
    assert webhook.request_kind == AutomationRequestKind.UNSAFE
    assert secret.request_kind == AutomationRequestKind.UNSAFE


def test_multiple_visible_brief_tasks_need_clarification():
    visible_tasks = [
        {"id": "daily_local_ai_security_briefing", "name": "Daily Local AI Security Briefing"},
        {"id": "weekly_security_digest", "name": "Weekly Security Digest"},
    ]

    result = classify_automation_request(
        "Pausiere den Brief.",
        visible_tasks=visible_tasks,
        task_aliases={},
    )

    assert result.request_kind == AutomationRequestKind.NEEDS_CLARIFICATION
    assert "target_task_id" in result.missing_information
    assert result.target_task_candidates == ["daily_local_ai_security_briefing", "weekly_security_digest"]


def test_run_brief_now_selects_only_enabled_matching_brief(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        if method == "GET" and path == "/tasks":
            return {
                "data": [
                    {"id": "daily_briefing_n8n_stub", "name": "Daily Briefing n8n Stub", "enabled": False, "status": "draft"},
                    {
                        "id": "daily_local_ai_security_briefing",
                        "name": "Daily Local AI Security Briefing",
                        "enabled": False,
                        "status": "paused",
                    },
                    {
                        "id": "twice_daily_ai_policy_security_brief",
                        "name": "Twice Daily AI, Security, and Policy Brief",
                        "enabled": True,
                        "status": "enabled",
                    },
                ]
            }
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: calls.append(payload) or {"answer": "Run queued."})

    answer = bragi.route_chat([{"role": "user", "content": "send brief now"}])

    assert calls == [{"action": "run_task", "task_id": "twice_daily_ai_policy_security_brief"}]
    assert "Run queued" in answer


def test_route_chat_uses_visible_task_name_before_legacy_alias(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        if method == "GET" and path == "/tasks":
            return {
                "data": [
                    {"id": "astronomy_and_astrophysics", "name": "Astronomy And Astrophysics Digest", "enabled": True, "status": "enabled"},
                    {"id": "daily_ai_stack_health", "name": "AI Stack Health Monitor", "enabled": True, "status": "enabled"},
                    {
                        "id": "twice_daily_ai_policy_security_brief",
                        "name": "Twice Daily AI, Security, and Policy Brief",
                        "enabled": True,
                        "status": "enabled",
                    },
                ]
            }
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: calls.append(payload) or {"answer": "Task shown."})

    answer = bragi.route_chat([{"role": "user", "content": "show the twice daily AI policy security brief"}])

    assert calls == [{"action": "show_task", "task_id": "twice_daily_ai_policy_security_brief"}]
    assert "Task shown" in answer


def test_run_existing_daily_brief_routes_to_yggdrasil(monkeypatch):
    calls = []

    def fake_yggdrasil(payload):
        calls.append(payload)
        return {"status": "ok", "answer": "Run queued."}

    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)

    answer = bragi.route_chat([{"role": "user", "content": "Schick den Daily Brief jetzt."}])

    assert calls == [{"action": "run_task", "task_id": "daily_local_ai_security_briefing"}]
    assert "Run queued" in answer


def test_run_existing_from_intake_selection_uses_selected_task_not_intake_id(monkeypatch):
    calls = []
    monkeypatch.setattr(bragi, "visible_tasks_for_goal_router", lambda: [])
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: calls.append(payload) or {"answer": "Run queued."})

    answer = bragi.update_goal_routing_intake_response(
        {
            "id": "bragi_intake_20260519_210303_4f697b01",
            "channel": "discord",
            "intent": {"user_request": "send brief now"},
        },
        "Intake: bragi_intake_20260519_210303_4f697b01: twice_daily_ai_policy_security_brief",
        user_id="local_user",
    )

    assert calls == [{"action": "run_task", "task_id": "twice_daily_ai_policy_security_brief"}]
    assert "Run queued" in answer


def test_pause_existing_health_check_routes_to_yggdrasil(monkeypatch):
    calls = []
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: calls.append(payload) or {"answer": "Paused."})

    answer = bragi.route_chat([{"role": "user", "content": "Pausiere den Health Check."}])

    assert calls == [{"action": "pause_task", "task_id": "morning_server_health_check"}]
    assert "Paused" in answer


def test_inspect_existing_daily_brief_routes_to_yggdrasil(monkeypatch):
    calls = []
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: calls.append(payload) or {"answer": "Task shown."})

    answer = bragi.route_chat([{"role": "user", "content": "Zeig mir den Daily Brief."}])

    assert calls == [{"action": "show_task", "task_id": "daily_local_ai_security_briefing"}]
    assert "Task shown" in answer


def test_modify_existing_security_brief_uses_source_selection_intake(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/tasks":
            return {"data": []}
        return source_registry_response(method, path, payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "Nimm CISA und NVD in den Security Brief auf."}])

    assert ("GET", "/sources", None) in calls
    assert "`cisa_news_events`" in answer
    assert "`nist_national_vulnerability_database`" in answer
    assert "awaiting_source_selection" in answer
    assert "Yggy confirmation and approval still control" in answer


def test_create_new_local_ai_brief_uses_topic_digest_capability(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "Erstelle mir jeden Werktag um 8 einen Local-AI Security Brief."}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["capability_id"] == "topic_digest.v1"
    assert calls[0][2]["slots"]["cron"] == "0 8 * * 1-5"
    assert calls[0][2]["slots"]["source_ids"]
    assert "Canonical intent pending confirmation" in answer


def test_create_new_server_health_uses_health_capability(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "Überwache Open WebUI und Ollama morgens."}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["capability_id"] == "server_health.v1"
    assert calls[0][2]["slots"]["check_ids"] == ["open_webui", "ollama"]
    assert "Canonical intent pending confirmation" in answer


def test_create_new_ai_stack_monitor_uses_health_capability(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "set up an AI stack monitor every morning"}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["capability_id"] == "server_health.v1"
    assert "Canonical intent pending confirmation" in answer


def test_unsafe_updates_and_restarts_are_rejected_without_forwarding(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("api called")))
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "Installiere automatisch Updates und starte Dienste neu."}])

    assert "outside Bragi's execution path" in answer
    assert "automatic updates" in answer or "service restarts" in answer


def test_ambiguous_existing_change_creates_goal_clarification_intake(monkeypatch):
    def fake_api_request(method, path, payload=None):
        if method == "GET" and path == "/tasks":
            return {
                "data": [
                    {"id": "daily_local_ai_security_briefing", "name": "Daily Local AI Security Briefing"},
                    {"id": "weekly_security_digest", "name": "Weekly Security Digest"},
                ]
            }
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "Mach den Brief besser."}])
    intake_id = bragi.intake_id_from_text(answer)

    assert "Possible existing automations" in answer
    assert "Add or remove approved sources" in answer
    assert "I have not sent anything to Yggdrasil" in answer
    assert intake_id is not None
    stored = intake_store.get_intake(intake_id=intake_id, user_id="local_user")
    assert stored["intent"]["intent"] == "automation_request_routing"
    assert stored["status"] == "collecting_slots"
    assert stored["summary"]["goal"]["kind"] == "automation_clarification"
    assert stored["summary"]["goal"]["request_kind"] == "needs_clarification"
    assert stored["summary"]["goal"]["target_task_candidates"] == [
        "daily_local_ai_security_briefing",
        "weekly_security_digest",
    ]


def test_goal_clarification_metadata_survives_show_and_continue(monkeypatch):
    def fake_api_request(method, path, payload=None):
        if method == "GET" and path == "/tasks":
            return {
                "data": [
                    {"id": "daily_local_ai_security_briefing", "name": "Daily Local AI Security Briefing"},
                    {"id": "weekly_security_digest", "name": "Weekly Security Digest"},
                ]
            }
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "Mach den Brief besser."}])
    intake_id = bragi.intake_id_from_text(answer)
    detail = bragi.route_chat([{"role": "user", "content": f"show intake {intake_id}"}])
    continued = bragi.route_chat([{"role": "user", "content": f"continue intake {intake_id}"}])
    cannot_confirm = bragi.route_chat([{"role": "user", "content": f"confirm intake {intake_id}"}])

    assert intake_id is not None
    assert "Goal: `needs_clarification`" in detail
    assert "Question:" in detail
    assert "Goal: `needs_clarification`" in continued
    assert "incomplete" in cannot_confirm
