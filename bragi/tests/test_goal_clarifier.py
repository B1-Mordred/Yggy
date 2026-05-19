from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bragi"))

from bragi import intake_store  # noqa: E402
from bragi import main as bragi  # noqa: E402
from bragi.goal_loop import classify_automation_request  # noqa: E402
from bragi.goal_models import AutomationRequestClassification, AutomationRequestKind, AutomationTargetKind  # noqa: E402
from bragi.hermes_client import HermesClarifierError  # noqa: E402


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

    answer = bragi.route_chat([{"role": "user", "content": "set up an AI stack monitor every morning"}])

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

    answer = bragi.route_chat([{"role": "user", "content": "set up an AI stack monitor every morning"}])

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
