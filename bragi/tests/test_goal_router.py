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


def test_what_tasks_question_routes_to_list_existing():
    result = classify_automation_request("What automation tasks do I have?", task_aliases=bragi.TASK_ALIASES)

    assert result.request_kind == AutomationRequestKind.LIST_EXISTING
    assert result.operation == {"action": "list_tasks"}


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


def test_run_existing_daily_brief_routes_to_yggdrasil(monkeypatch):
    calls = []

    def fake_yggdrasil(payload):
        calls.append(payload)
        return {"status": "ok", "answer": "Run queued."}

    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)

    answer = bragi.route_chat([{"role": "user", "content": "Schick den Daily Brief jetzt."}])

    assert calls == [{"action": "run_task", "task_id": "daily_local_ai_security_briefing"}]
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
        return source_registry_response(method, path, payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "Nimm CISA und NVD in den Security Brief auf."}])

    assert calls == [("GET", "/sources", None)]
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
