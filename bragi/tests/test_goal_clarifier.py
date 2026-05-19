from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bragi"))

from bragi import intake_store  # noqa: E402
from bragi import clarifier_api  # noqa: E402
from bragi import main as bragi  # noqa: E402
from bragi.goal_loop import classify_automation_request  # noqa: E402
from bragi.goal_models import AutomationRequestClassification, AutomationRequestKind, AutomationTargetKind  # noqa: E402
from bragi.hermes_client import HermesClarifierError, normalize_classification_payload  # noqa: E402


@pytest.fixture(autouse=True)
def reset_bragi_intakes():
    intake_store.reset_intake_store_for_tests()


class BrokenHermes:
    def classify_request(self, **kwargs):
        raise HermesClarifierError("invalid json")


class FakeHermes:
    def __init__(self, classification: AutomationRequestClassification):
        self.classification = classification

    def classify_request(self, **kwargs):
        return self.classification


def server_health_candidate(**slot_overrides):
    slots = {
        "task_id": "daily_ai_stack_health",
        "name": "Daily AI Stack Health Check",
        "cron": "0 8 * * *",
        "timezone": "Europe/Berlin",
        "check_ids": ["open_webui", "ollama"],
        "output_target": "alerts",
        **slot_overrides,
    }
    return {
        "intent": "draft_task",
        "capability_id": "server_health.v1",
        "confidence": 0.91,
        "requires_user_confirmation": True,
        "user_confirmation_obtained": True,
        "slots": slots,
    }


def test_hermes_unavailable_or_invalid_json_falls_back_to_deterministic_classifier():
    result = classify_automation_request(
        "list workflows",
        task_aliases=bragi.TASK_ALIASES,
        use_hermes=True,
        hermes_client=BrokenHermes(),
    )

    assert result.request_kind == AutomationRequestKind.LIST_EXISTING
    assert result.operation == {"action": "list_tasks"}


def test_goal_clarifier_client_uses_dedicated_api_key(monkeypatch):
    created = {}
    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_ENABLED", True)
    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_PROVIDER", "hermes")
    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_BASE_URL", "http://clarifier.local:8651")
    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_MODEL", "llama3.1:8b")
    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_TIMEOUT", 12)
    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_API_KEY", "clarifier-only-key")

    class CapturingClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(bragi, "HermesClarifierClient", CapturingClient)

    assert bragi.goal_clarifier_client() is not None
    assert created == {
        "base_url": "http://clarifier.local:8651",
        "model": "llama3.1:8b",
        "timeout": 12,
        "api_key": "clarifier-only-key",
    }


def test_hermes_payload_normalization_handles_loose_json_fields():
    normalized = normalize_classification_payload(
        {
            "request_kind": "chat",
            "target_kind": "unknown",
            "reason": None,
            "confidence": None,
            "target_task_candidates": None,
            "missing_information": "target_task_id",
            "assumptions": None,
            "unsafe_reasons": None,
            "operation": "run_task",
            "candidate_intent": "draft_task",
        }
    )

    assert AutomationRequestClassification.model_validate(normalized).request_kind == AutomationRequestKind.CHAT
    assert normalized["reason"] == ""
    assert normalized["confidence"] == 0.0
    assert normalized["missing_information"] == []
    assert normalized["operation"] is None
    assert normalized["candidate_intent"] is None

    with_task_id = normalize_classification_payload(
        {
            "request_kind": "run_existing",
            "target_kind": "existing_task",
            "operation": {"action": "run_task", "task_id": "daily_local_ai_security_briefing"},
        }
    )
    assert with_task_id["target_task_id"] == "daily_local_ai_security_briefing"


def test_dedicated_clarifier_api_classifies_from_prompt_payload():
    prompt = {
        "latest_user_request": "send daily brief now",
        "visible_tasks": [{"id": "daily_local_ai_security_briefing", "name": "Daily Local AI Security Briefing"}],
        "task_aliases": {"daily brief": "daily_local_ai_security_briefing"},
        "capability_ids": ["topic_digest.v1"],
    }

    result = clarifier_api.classify_from_messages(
        [{"role": "user", "content": "Classify this request.\n\n" + __import__("json").dumps(prompt)}]
    )

    assert result.request_kind == AutomationRequestKind.RUN_EXISTING
    assert result.operation == {"action": "run_task", "task_id": "daily_local_ai_security_briefing"}


def test_hermes_candidate_is_validated_and_cannot_bypass_user_confirmation(monkeypatch):
    calls = []
    classification = AutomationRequestClassification(
        request_kind=AutomationRequestKind.CREATE_NEW,
        target_kind=AutomationTargetKind.NEW_TASK,
        capability_id="server_health.v1",
        candidate_intent=server_health_candidate(),
        confidence=0.91,
        reason="Hermes mapped the request to a known server health capability.",
    )

    def fake_api_request(method, path, payload=None):
        if method == "GET" and path == "/tasks":
            return {"data": []}
        calls.append((method, path, payload))
        assert payload["user_confirmation_obtained"] is False
        return {
            "outcome": "ASK_CLARIFICATION",
            "capability_id": "server_health.v1",
            "message": "User confirmation is required.",
            "missing_slots": ["user_confirmation"],
            "confirmation_summary": {
                "capability_id": "server_health.v1",
                "task_id": payload["slots"]["task_id"],
                "name": payload["slots"]["name"],
                "schedule": {"cron": payload["slots"]["cron"], "timezone": payload["slots"]["timezone"]},
                "checks": payload["slots"]["check_ids"],
                "output_target": payload["slots"]["output_target"],
                "dry_run": True,
                "approval_level": "L1_NOTIFY_ONLY",
                "worst_case_failure_mode": "A noisy alert could be sent.",
                "rollback_disable_method": "Pause through /ops.",
            },
        }

    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_ENABLED", True)
    monkeypatch.setattr(bragi, "goal_clarifier_client", lambda: FakeHermes(classification))
    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "set up a custom maintenance automation"}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["requires_user_confirmation"] is True
    assert calls[0][2]["user_confirmation_obtained"] is False
    assert "Canonical intent pending confirmation" in answer


def test_hermes_unsafe_slots_go_through_heimdal_rejection(monkeypatch):
    calls = []
    classification = AutomationRequestClassification(
        request_kind=AutomationRequestKind.CREATE_NEW,
        target_kind=AutomationTargetKind.NEW_TASK,
        capability_id="server_health.v1",
        candidate_intent=server_health_candidate(allow_shell=True),
        confidence=0.90,
        reason="Hermes suggested a candidate with unsafe slots.",
    )

    def fake_api_request(method, path, payload=None):
        if method == "GET" and path == "/tasks":
            return {"data": []}
        calls.append((method, path, payload))
        return {
            "outcome": "REJECT_UNSAFE",
            "capability_id": "server_health.v1",
            "message": "unsafe",
            "unsafe_reasons": ["allow_shell is forbidden"],
            "confirmation_summary": {},
        }

    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_ENABLED", True)
    monkeypatch.setattr(bragi, "goal_clarifier_client", lambda: FakeHermes(classification))
    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "set up a custom maintenance automation"}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["slots"]["allow_shell"] is True
    assert "allow_shell is forbidden" in answer
    assert "outside the allowed automation path" in answer


def test_hermes_invalid_capability_is_not_executed(monkeypatch):
    calls = []
    classification = AutomationRequestClassification(
        request_kind=AutomationRequestKind.CREATE_NEW,
        target_kind=AutomationTargetKind.NEW_TASK,
        capability_id="credential_rotation.v1",
        candidate_intent={
            "intent": "draft_task",
            "capability_id": "credential_rotation.v1",
            "confidence": 0.88,
            "requires_user_confirmation": True,
            "user_confirmation_obtained": True,
            "slots": {"task_id": "rotate_credentials", "name": "Rotate Credentials"},
        },
        confidence=0.88,
        reason="Hermes suggested an unregistered capability.",
    )

    def fake_api_request(method, path, payload=None):
        if method == "GET" and path == "/tasks":
            return {"data": []}
        calls.append((method, path, payload))
        return {
            "outcome": "REJECT_UNSUPPORTED",
            "capability_id": payload["capability_id"],
            "message": "unknown capability",
        }

    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_ENABLED", True)
    monkeypatch.setattr(bragi, "goal_clarifier_client", lambda: FakeHermes(classification))
    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "set up a credential-maintenance reminder"}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["capability_id"] == "credential_rotation.v1"
    assert "not a registered executable Yggy capability" in answer


def test_hermes_cannot_downgrade_deterministic_capability_proposal_to_clarification(monkeypatch):
    classification = AutomationRequestClassification(
        request_kind=AutomationRequestKind.NEEDS_CLARIFICATION,
        target_kind=AutomationTargetKind.UNKNOWN,
        missing_information=["data_source"],
        confidence=0.41,
        reason="Hermes wanted more information.",
    )

    monkeypatch.setattr(bragi, "GOAL_CLARIFIER_ENABLED", True)
    monkeypatch.setattr(bragi, "goal_clarifier_client", lambda: FakeHermes(classification))
    monkeypatch.setattr(bragi, "api_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("api called")))
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "track UPS battery status and alert me"}])

    assert "not a registered executable Yggy capability yet" in answer
    assert "Nothing was sent to Yggdrasil" in answer
