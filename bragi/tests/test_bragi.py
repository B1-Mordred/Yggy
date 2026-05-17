from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bragi"))

from bragi import main as bragi  # noqa: E402


def gateway_response_for(payload: dict) -> dict:
    if payload["user_request"].lower().startswith("restart docker"):
        return {
            "outcome": "REJECT_UNSAFE",
            "capability_id": "server_health.v1",
            "message": "unsafe",
            "unsafe_reasons": ["unsafe keyword or capability: restart docker"],
            "confirmation_summary": {},
        }
    if "printer" in payload["user_request"].lower():
        return {
            "outcome": "PROPOSE_NEW_CAPABILITY",
            "capability_id": "server_health.v1",
            "message": "This looks useful, but no printer or toner capability is registered yet.",
        }
    missing = []
    if payload["capability_id"] == "n8n_webhook.v1" and not payload["slots"].get("webhook_id"):
        missing.append("webhook_id")
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
                "webhook_id": payload["slots"].get("webhook_id"),
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
        "yggdrasil_request": {
            "action": "draft_task_from_template",
            "capability_id": payload["capability_id"],
            "template_id": payload["capability_id"].split(".")[0],
            "template_values": {"id": payload["slots"]["task_id"], "name": payload["slots"]["name"]},
        },
    }


def test_natural_server_health_request_asks_for_confirmation(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat(
        [{"role": "user", "content": "Can you keep an eye on my AI server and tell me if something is broken?"}]
    )

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["capability_id"] == "server_health.v1"
    assert "Reply `confirm`" in answer
    assert "Canonical intent pending confirmation" in answer


def test_confirmation_forwards_only_canonical_request(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    def fake_yggdrasil(payload):
        calls.append(("POST", "/v1/yggdrasil/canonical-actions", payload))
        return {"status": "ok", "answer": "Draft task `daily_ai_stack_health` was created."}

    pending = bragi.server_health_intent("Can you keep an eye on my AI server?")
    prior = "Canonical intent pending confirmation:\n```json\n" + json.dumps(pending) + "\n```"
    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)

    answer = bragi.route_chat(
        [
            {"role": "assistant", "content": prior},
            {"role": "user", "content": "confirm"},
        ]
    )

    assert calls[0][0:2] == ("POST", "/capabilities/prepare-yggdrasil-request")
    assert calls[0][2]["user_confirmation_obtained"] is True
    assert calls[1][0:2] == ("POST", "/v1/yggdrasil/canonical-actions")
    assert "user_request" not in calls[1][2]["template_values"]
    assert "Draft task" in answer


def test_unsafe_request_is_not_forwarded(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "Restart Docker whenever something looks wrong."}])

    assert "outside the allowed automation path" in answer
    assert "restart docker" in answer.lower()


def test_printer_toner_becomes_new_capability_proposal(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", lambda method, path, payload=None: gateway_response_for(payload))

    answer = bragi.route_chat([{"role": "user", "content": "Check my printer toner and warn me before it runs out."}])

    assert "no printer or toner capability" in answer
    assert "new capability proposal" in answer


def test_n8n_request_without_known_webhook_asks_clarification(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", lambda method, path, payload=None: gateway_response_for(payload))

    answer = bragi.route_chat([{"role": "user", "content": "Create an n8n webhook task for my new workflow."}])

    assert "`webhook_id`" in answer


def test_general_chat_does_not_claim_capability_failure(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        raise AssertionError("general chat should not call Heimdal")

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "general_chat_answer", lambda messages: "Hello. Pull up a chair.")

    answer = bragi.route_chat([{"role": "user", "content": "hello there"}])

    assert answer == "Hello. Pull up a chair."
    assert calls == []
    assert "cannot map" not in answer.lower()
    assert "yggdrasil" not in answer.lower()


def test_how_to_brief_question_stays_in_general_chat(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        raise AssertionError("help questions should not call Heimdal")

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "general_chat_answer", lambda messages: "You can describe the topic you want to add.")

    answer = bragi.route_chat([{"role": "user", "content": "how can i add a new subject to the brief?"}])

    assert answer == "You can describe the topic you want to add."
    assert calls == []


def test_direct_brief_draft_still_routes_to_gateway(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat(
        [{"role": "user", "content": "Draft a weekday 08:00 local AI security briefing to Discord, keep it disabled."}]
    )

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["capability_id"] == "topic_digest.v1"
    assert "Reply `confirm`" in answer


def test_simple_greeting_does_not_call_ollama(monkeypatch):
    monkeypatch.setattr(bragi, "ollama_chat", lambda messages: (_ for _ in ()).throw(AssertionError("called ollama")))

    answer = bragi.general_chat_answer([{"role": "user", "content": "hello there"}])

    assert "Hello" in answer
    assert "cannot map" not in answer.lower()


def test_yggdrasil_unauthorized_message_is_specific(monkeypatch):
    class Response:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {}

    class Client:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            return Response()

    monkeypatch.setattr(bragi.httpx, "Client", Client)

    result = bragi.yggdrasil_canonical_request({"action": "draft_task_from_template"})

    assert result["status"] == "unauthorized"
    assert "not authorized to talk to Yggdrasil" in result["answer"]
