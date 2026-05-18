from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bragi"))

from bragi import main as bragi  # noqa: E402
from bragi import intake_store  # noqa: E402
from bragi import memory_store  # noqa: E402


@pytest.fixture(autouse=True)
def reset_bragi_stores():
    intake_store.reset_intake_store_for_tests()


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
    if payload["capability_id"] == "topic_digest.v1" and not payload["slots"].get("source_ids"):
        missing.append("source_ids")
    if payload["capability_id"] == "topic_digest.modify_subjects.v1" and not any(
        payload["slots"].get(slot)
        for slot in ("add_source_ids", "remove_source_ids", "add_include", "remove_include")
    ):
        missing.append("subject_change")
    if payload["capability_id"] == "server_health.v1" and not payload["slots"].get("check_ids"):
        missing.append("check_ids")
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
                "change_type": "topic_digest_subjects" if payload["capability_id"] == "topic_digest.modify_subjects.v1" else None,
                "add_source_ids": payload["slots"].get("add_source_ids", []),
                "remove_source_ids": payload["slots"].get("remove_source_ids", []),
                "add_include": payload["slots"].get("add_include", []),
                "remove_include": payload["slots"].get("remove_include", []),
                "webhook_id": payload["slots"].get("webhook_id"),
                "output_target": payload["slots"].get("output_target"),
                "dry_run": True,
                "approval_level": "L1_NOTIFY_ONLY",
                "worst_case_failure_mode": "A noisy alert could be sent.",
                "rollback_disable_method": "Pause through /ops.",
            },
        }
    if payload["capability_id"] == "topic_digest.modify_subjects.v1":
        return {
            "outcome": "ACCEPT",
            "capability_id": payload["capability_id"],
            "message": "accepted",
            "confirmation_summary": {},
            "yggdrasil_request": {
                "action": "propose_task_change",
                "capability_id": payload["capability_id"],
                "task_id": payload["slots"]["task_id"],
                "change_type": "topic_digest_subjects",
                "change": {
                    "add_source_ids": payload["slots"].get("add_source_ids", []),
                    "remove_source_ids": payload["slots"].get("remove_source_ids", []),
                    "add_include": payload["slots"].get("add_include", []),
                    "remove_include": payload["slots"].get("remove_include", []),
                },
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
    monkeypatch.setattr(bragi, "general_chat_answer", lambda messages, **kwargs: "Hello. Pull up a chair.")

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
    monkeypatch.setattr(bragi, "general_chat_answer", lambda messages, **kwargs: "You can describe the topic you want to add.")

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


def test_add_subject_to_existing_brief_routes_as_task_change(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "add Docker security updates to the daily brief"}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    intent = calls[0][2]
    assert intent["intent"] == "propose_task_change"
    assert intent["capability_id"] == "topic_digest.modify_subjects.v1"
    assert intent["slots"]["task_id"] == "daily_local_ai_security_briefing"
    assert intent["slots"]["add_source_ids"] == ["docker_blog"]
    assert intent["slots"]["add_include"] == ["Docker security updates"]
    assert "task-change proposal" in answer
    assert "Reply `confirm`" in answer


def source_catalog_fixture(method, path, payload=None):
    if method == "GET" and path == "/sources":
        return {
            "data": [
                {
                    "id": "cisa_news_events",
                    "name": "CISA News & Events",
                    "type": "http",
                    "enabled": True,
                    "categories": ["preapproved", "cybersecurity"],
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
                    "categories": ["preapproved", "cybersecurity"],
                    "trust_level": "ai_safe_a_open",
                    "ai_safe_fit": "A - high-fit/open",
                    "ingestion_mode": "http_summary",
                    "description": "CVE vulnerability enrichment and CVSS metrics.",
                },
                {
                    "id": "cisa_known_exploited_vulnerabilities_catalog",
                    "name": "CISA Known Exploited Vulnerabilities Catalog",
                    "type": "http",
                    "enabled": True,
                    "categories": ["preapproved", "cybersecurity"],
                    "trust_level": "ai_safe_a_open",
                    "ai_safe_fit": "A - high-fit/open",
                    "ingestion_mode": "http_summary",
                    "description": "Known exploited vulnerabilities catalog.",
                },
                {
                    "id": "ubuntu_security_notices",
                    "name": "Ubuntu Security Notices",
                    "type": "http",
                    "enabled": True,
                    "categories": ["preapproved", "cybersecurity"],
                    "trust_level": "ai_safe_b_terms_check",
                    "ai_safe_fit": "B - terms-check/variable",
                    "ingestion_mode": "metadata_only",
                    "description": "Canonical/Ubuntu security notices and CVE package status.",
                },
                {
                    "id": "ollama_releases",
                    "name": "Ollama releases",
                    "type": "rss",
                    "enabled": True,
                    "categories": ["local_ai", "project_releases"],
                    "trust_level": "official_project_release_feed",
                    "ai_safe_fit": "",
                    "ingestion_mode": "feed_metadata",
                    "description": "Official Ollama release feed.",
                },
                {
                    "id": "open_webui_releases",
                    "name": "Open WebUI releases",
                    "type": "rss",
                    "enabled": True,
                    "categories": ["local_ai", "project_releases"],
                    "trust_level": "official_project_release_feed",
                    "ai_safe_fit": "",
                    "ingestion_mode": "feed_metadata",
                    "description": "Official Open WebUI release feed.",
                },
                {
                    "id": "n8n_releases",
                    "name": "n8n releases",
                    "type": "rss",
                    "enabled": True,
                    "categories": ["workflow_automation", "project_releases"],
                    "trust_level": "official_project_release_feed",
                    "ai_safe_fit": "",
                    "ingestion_mode": "feed_metadata",
                    "description": "Official n8n release feed.",
                },
                {
                    "id": "docker_blog",
                    "name": "Docker blog",
                    "type": "rss",
                    "enabled": True,
                    "categories": ["containers", "security_news"],
                    "trust_level": "official_vendor_blog",
                    "ai_safe_fit": "",
                    "ingestion_mode": "feed_metadata",
                    "description": "Docker security and platform news.",
                },
            ]
        }
    return gateway_response_for(payload)


def test_catalog_source_names_are_resolved_before_canonical_intent(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return source_catalog_fixture(method, path, payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    answer = bragi.route_chat([{"role": "user", "content": "add CISA and NVD to the security brief"}])

    assert calls == [("GET", "/sources", None)]
    assert "`cisa_news_events`" in answer
    assert "`nist_national_vulnerability_database`" in answer
    assert "mode `http_summary`" in answer
    assert "Intake:" in answer
    assert "awaiting_source_selection" in answer
    assert "Canonical intent" not in answer


def test_source_selection_intake_can_be_updated_by_number(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return source_catalog_fixture(method, path, payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "add CISA and NVD to the security brief"}])
    intake_id = bragi.intake_id_from_text(answer)

    assert intake_id is not None

    updated = bragi.route_chat([{"role": "user", "content": f"use sources 2 and 3 for intake {intake_id}"}])

    assert calls[-1][0:2] == ("POST", "/capabilities/validate-intent")
    intent = calls[-1][2]
    assert intent["capability_id"] == "topic_digest.modify_subjects.v1"
    assert intent["slots"]["add_source_ids"] == [
        "cisa_known_exploited_vulnerabilities_catalog",
        "nist_national_vulnerability_database",
    ]
    assert "Canonical intent pending confirmation" in updated
    assert f"confirm intake {intake_id}" in updated
    stored = intake_store.get_intake(intake_id=intake_id, user_id="local_user")
    assert stored["status"] == "awaiting_confirmation"


def test_confirmed_source_selection_generates_task_change_intent(monkeypatch):
    calls = []
    selection = {
        "source_selection_action": "confirm_topic_digest_sources",
        "capability_id": "topic_digest.modify_subjects.v1",
        "task_id": "daily_local_ai_security_briefing",
        "selected_source_ids": ["cisa_news_events", "nist_national_vulnerability_database"],
        "include_terms": ["CISA", "NVD"],
        "original_request": "add CISA and NVD to the security brief",
    }
    prior = "Pending source selection:\n```json\n" + json.dumps(selection) + "\n```"

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat(
        [
            {"role": "assistant", "content": prior},
            {"role": "user", "content": "confirm sources"},
        ]
    )

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    intent = calls[0][2]
    assert intent["intent"] == "propose_task_change"
    assert intent["capability_id"] == "topic_digest.modify_subjects.v1"
    assert intent["slots"]["add_source_ids"] == ["cisa_news_events", "nist_national_vulnerability_database"]
    assert intent["slots"]["add_include"] == ["CISA", "NVD"]
    assert "Canonical intent pending confirmation" in answer
    assert "Reply `confirm`" in answer


def test_conversational_security_briefing_intake_becomes_canonical_intent(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return source_catalog_fixture(method, path, payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    messages = [
        {"role": "user", "content": "daring to dream of automating something"},
        {"role": "assistant", "content": "What is the dream automation?"},
        {"role": "user", "content": "a morning briefing about relevant threats could teach me about what to expect"},
        {"role": "assistant", "content": "What sources should Yggy use?"},
        {"role": "user", "content": "need it security related information about ubuntu 26, hermes, ollama"},
        {
            "role": "user",
            "content": "official blog posts, vulnerability announcements, patch notes, nvd records, no gossip, update me for breakfast on a daily basis",
        },
    ]

    answer = bragi.route_chat(messages)

    assert calls[0][0:2] == ("GET", "/sources")
    assert calls[1][0:2] == ("POST", "/capabilities/validate-intent")
    intent = calls[1][2]
    assert intent["intent"] == "draft_task"
    assert intent["capability_id"] == "topic_digest.v1"
    assert intent["slots"]["task_id"] == "daily_security_threat_briefing"
    assert intent["slots"]["cron"] == "0 8 * * *"
    assert "ubuntu_security_notices" in intent["slots"]["source_ids"]
    assert "ollama_releases" in intent["slots"]["source_ids"]
    assert "nist_national_vulnerability_database" in intent["slots"]["source_ids"]
    assert "cisa_news_events" in intent["slots"]["source_ids"]
    assert "gossip" in intent["slots"]["exclude"]
    assert "Hermes" in intent["slots"]["include"]
    assert "Canonical intent pending confirmation" in answer
    assert "Reply `confirm`" in answer


def test_conversational_confirmation_without_pending_intent_shows_intent_first(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return source_catalog_fixture(method, path, payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("forwarded")))

    messages = [
        {"role": "user", "content": "I want a daily morning briefing about Ubuntu 26, Hermes, Ollama security threats"},
        {"role": "assistant", "content": "Should I use official blog posts, vulnerability announcements, patch notes, and NVD records?"},
        {"role": "user", "content": "so be it"},
    ]

    answer = bragi.route_chat(messages)

    assert calls[0][0:2] == ("GET", "/sources")
    assert calls[1][0:2] == ("POST", "/capabilities/validate-intent")
    assert "Canonical intent pending confirmation" in answer
    assert "Reply `confirm`" in answer


def test_intake_id_confirmation_reloads_stored_intent(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return source_catalog_fixture(method, path, payload)

    def fake_yggdrasil(payload):
        calls.append(("POST", "/v1/yggdrasil/canonical-actions", payload))
        return {"status": "ok", "answer": "Draft task `daily_security_threat_briefing` was created."}

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)

    answer = bragi.route_chat(
        [
            {"role": "user", "content": "I want a daily morning briefing about Ubuntu 26, Hermes, Ollama security threats"},
            {"role": "assistant", "content": "Should I use official sources?"},
            {"role": "user", "content": "official blog posts, vulnerability announcements, patch notes, nvd records, no gossip"},
        ]
    )
    intake_id = bragi.intake_id_from_text(answer)

    assert intake_id is not None
    assert f"confirm intake {intake_id}" in answer

    confirm_answer = bragi.route_chat([{"role": "user", "content": f"confirm intake {intake_id}"}])

    assert calls[-2][0:2] == ("POST", "/capabilities/prepare-yggdrasil-request")
    assert calls[-2][2]["user_confirmation_obtained"] is True
    assert calls[-1][0:2] == ("POST", "/v1/yggdrasil/canonical-actions")
    assert "user_request" not in json.dumps(calls[-1][2])
    assert "Draft task" in confirm_answer
    stored = intake_store.get_intake(intake_id=intake_id, user_id="local_user")
    assert stored["status"] == "forwarded_to_yggdrasil"


def test_intake_list_show_and_cancel(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", lambda method, path, payload=None: source_catalog_fixture(method, path, payload))

    answer = bragi.route_chat(
        [
            {"role": "user", "content": "I want a daily morning briefing about Ubuntu 26 and Ollama security threats"},
            {"role": "assistant", "content": "Any source preferences?"},
            {"role": "user", "content": "use vulnerability announcements and nvd records"},
        ]
    )
    intake_id = bragi.intake_id_from_text(answer)

    listing = bragi.route_chat([{"role": "user", "content": "show pending intakes"}])
    detail = bragi.route_chat([{"role": "user", "content": f"show intake {intake_id}"}])
    cancelled = bragi.route_chat([{"role": "user", "content": f"cancel intake {intake_id}"}])
    confirm_after_cancel = bragi.route_chat([{"role": "user", "content": f"confirm intake {intake_id}"}])

    assert intake_id in listing
    assert "Canonical intent" in detail
    assert "Cancelled intake" in cancelled
    assert "because it is `cancelled`" in confirm_after_cancel


def test_intake_endpoints_require_key_and_redact(monkeypatch):
    monkeypatch.setattr(bragi, "API_KEY", "test-bragi-key")
    client = TestClient(bragi.app)
    intake = intake_store.create_intake(
        user_id="local_user",
        intent=bragi.topic_digest_intent("Draft a weekday 08:00 local AI security briefing"),
        summary={"task_id": "daily_local_ai_security_briefing"},
    )

    unauthorized = client.post("/intakes/query", json={"user_id": "local_user"})
    authorized = client.post(
        "/intakes/query",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"user_id": "local_user"},
    )
    detail = client.post(
        "/intakes/get",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"user_id": "local_user", "intake_id": intake["id"]},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["records"][0]["id"] == intake["id"]
    assert detail.status_code == 200
    assert detail.json()["record"]["id"] == intake["id"]


def test_freeform_yggdrasil_message_is_refused_without_forwarding(monkeypatch):
    calls = []

    def fake_yggdrasil(payload):
        calls.append(payload)
        raise AssertionError("free-form message forwarded")

    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)

    answer = bragi.route_chat([{"role": "user", "content": "inform yggdrasil that i expect to hear from him regarding this"}])

    assert calls == []
    assert "cannot send a free-form side message to Yggdrasil" in answer
    assert "canonical intent" in answer


def test_confirmed_brief_subject_change_forwards_canonical_proposal(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    def fake_yggdrasil(payload):
        calls.append(("POST", "/v1/yggdrasil/canonical-actions", payload))
        return {"status": "ok", "answer": "Task change proposal created for the existing topic digest."}

    pending = bragi.topic_digest_subject_change_intent("add Docker security updates to the daily brief")
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
    assert calls[1][2]["action"] == "propose_task_change"
    assert calls[1][2]["task_id"] == "daily_local_ai_security_briefing"
    assert "user_request" not in json.dumps(calls[1][2])
    assert "Task change proposal created" in answer


def test_vague_subject_change_asks_for_subject_details(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "add a subject to the daily brief"}])

    assert calls[0][2]["capability_id"] == "topic_digest.modify_subjects.v1"
    assert "`subject_change`" in answer
    assert "Canonical intent awaiting details" in answer


def test_discussion_summary_does_not_draft_digest(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        raise AssertionError("discussion should not call Heimdal")

    monkeypatch.setattr(bragi, "api_request", fake_api_request)
    monkeypatch.setattr(bragi, "general_chat_answer", lambda messages, **kwargs: "Let us discuss Docker security.")

    answer = bragi.route_chat([{"role": "user", "content": "summarize Docker security risks for me"}])

    assert answer == "Let us discuss Docker security."
    assert calls == []


def test_vague_topic_digest_asks_for_missing_sources(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "draft a weekday 08:00 topic digest about German politics"}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["capability_id"] == "topic_digest.v1"
    assert calls[0][2]["slots"]["source_ids"] == []
    assert "`source_ids`" in answer
    assert "Intake:" in answer
    assert "Canonical intent awaiting details" in answer


def test_missing_slot_followup_can_update_stored_intake(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "draft a weekday 08:00 topic digest about German politics"}])
    intake_id = bragi.intake_id_from_text(answer)
    followup = bragi.route_chat([{"role": "user", "content": f"use docker_blog and send it to briefings for intake {intake_id}"}])

    assert intake_id is not None
    assert calls[-1][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[-1][2]["slots"]["source_ids"] == ["docker_blog"]
    assert "Canonical intent pending confirmation" in followup
    assert f"confirm intake {intake_id}" in followup
    stored = intake_store.get_intake(intake_id=intake_id, user_id="local_user")
    assert stored["status"] == "awaiting_confirmation"


def test_missing_slot_followup_merges_details(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    pending = bragi.topic_digest_intent("draft a weekday 08:00 topic digest about Docker")
    pending["slots"]["source_ids"] = []
    prior = "Canonical intent awaiting details:\n```json\n" + json.dumps(pending) + "\n```"
    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat(
        [
            {"role": "assistant", "content": prior},
            {"role": "user", "content": "use docker_blog and send it to briefings"},
        ]
    )

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert calls[0][2]["slots"]["source_ids"] == ["docker_blog"]
    assert "Reply `confirm`" in answer


def test_run_request_uses_structured_yggdrasil_operation(monkeypatch):
    calls = []

    def fake_yggdrasil(payload):
        calls.append(payload)
        return {"status": "ok", "answer": "Run queued for task `daily_local_ai_security_briefing`."}

    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)

    answer = bragi.route_chat([{"role": "user", "content": "send daily brief now"}])

    assert calls == [{"action": "run_task", "task_id": "daily_local_ai_security_briefing"}]
    assert "Run queued" in answer


def test_list_tasks_uses_structured_yggdrasil_operation(monkeypatch):
    calls = []

    def fake_yggdrasil(payload):
        calls.append(payload)
        return {"status": "ok", "answer": "Automation tasks:"}

    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)

    answer = bragi.route_chat([{"role": "user", "content": "list my automation tasks"}])

    assert calls == [{"action": "list_tasks"}]
    assert "Automation tasks" in answer


def test_show_explicit_task_id_uses_structured_yggdrasil_operation(monkeypatch):
    calls = []

    def fake_yggdrasil(payload):
        calls.append(payload)
        return {"status": "ok", "answer": "Task `daily_local_ai_security_briefing`"}

    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)

    answer = bragi.route_chat([{"role": "user", "content": "show task daily_local_ai_security_briefing"}])

    assert calls == [{"action": "show_task", "task_id": "daily_local_ai_security_briefing"}]
    assert "daily_local_ai_security_briefing" in answer


def test_memory_rejects_secret_like_material(tmp_path, monkeypatch):
    path = tmp_path / "memory.yaml"
    path.write_text("preferred_language: en\napi_key: nope\n", encoding="utf-8")
    monkeypatch.setattr(bragi, "MEMORY_FILE", str(path))

    assert bragi.load_memory() == {}


def test_memory_context_loads_non_secret_preferences(tmp_path, monkeypatch):
    path = tmp_path / "memory.yaml"
    path.write_text("preferred_language: en\ndefault_timezone: Europe/Berlin\nignored: value\n", encoding="utf-8")
    monkeypatch.setattr(bragi, "MEMORY_FILE", str(path))

    context = bragi.memory_context()

    assert "preferred_language" in context
    assert "Europe/Berlin" in context
    assert "ignored" not in context


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


def test_route_diagnostic_for_operation_is_read_only():
    diagnostic = bragi.diagnose_route([{"role": "user", "content": "send daily brief now"}])

    assert diagnostic["mode"] == "operation"
    assert diagnostic["route"] == "yggdrasil_canonical_action"
    assert diagnostic["operation"] == {"action": "run_task", "task_id": "daily_local_ai_security_briefing"}
    assert diagnostic["calls_external_services"] is False


def test_route_diagnostic_for_help_stays_general_chat():
    diagnostic = bragi.diagnose_route([{"role": "user", "content": "how can i add a new subject to the brief?"}])

    assert diagnostic["mode"] == "help"
    assert diagnostic["route"] == "general_chat"
    assert "conversational" in diagnostic["reason"]


def test_route_diagnostic_for_draft_omits_raw_user_request():
    diagnostic = bragi.diagnose_route(
        [{"role": "user", "content": "draft a weekday 08:00 topic digest about German politics"}]
    )

    assert diagnostic["mode"] == "draft"
    assert diagnostic["route"] == "heimdal_validate_intent"
    assert diagnostic["candidate_intent"]["capability_id"] == "topic_digest.v1"
    assert "user_request" not in diagnostic["candidate_intent"]


def test_route_diagnostic_for_intake_detail_update_stays_intake_management():
    intake = intake_store.create_intake(
        user_id="local_user",
        status="collecting_slots",
        intent=bragi.topic_digest_intent("draft a weekday 08:00 topic digest about German politics"),
        summary={"task_id": "weekday_topic_digest", "missing_slots": ["source_ids"]},
    )

    diagnostic = bragi.diagnose_route(
        [{"role": "user", "content": f"use docker_blog and send it to briefings for intake {intake['id']}"}]
    )

    assert diagnostic["mode"] == "intake_management"
    assert diagnostic["route"] == "bragi_intake_management"
    assert diagnostic["intake_id"] == intake["id"]


def test_route_diagnostic_chat_command_formats_result():
    answer = bragi.route_chat([{"role": "user", "content": "diagnose route: send daily brief now"}])

    assert "Bragi route diagnostic" in answer
    assert "yggdrasil_canonical_action" in answer
    assert "run_task" in answer


def test_route_diagnostics_endpoint_requires_bragi_key(monkeypatch):
    monkeypatch.setattr(bragi, "API_KEY", "test-bragi-key")
    client = TestClient(bragi.app)

    unauthorized = client.post("/diagnostics/route", json={"text": "send daily brief now"})
    authorized = client.post(
        "/diagnostics/route",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"text": "send daily brief now"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["operation"]["action"] == "run_task"


def context_api_fixture(method, path, payload=None):
    if method == "POST" and path == "/research/query":
        return {
            "read_only": True,
            "source_content_is_untrusted": True,
            "source_ids": ["open_webui_releases"],
            "items": [
                {
                    "id": "research-1",
                    "source_id": "open_webui_releases",
                    "source_name": "Open WebUI releases",
                    "title": "Open WebUI security release",
                    "summary": "Fixes relevant local AI behavior.",
                    "url": "https://example.com/release",
                    "metadata": {"source_content_is_untrusted": True},
                }
            ],
            "errors": [],
        }
    if method == "POST" and path == "/research/topic-digest-suggestion":
        return {
            "read_only": True,
            "source_content_is_untrusted": True,
            "suggestion_type": "topic_digest_slots",
            "suggested_slots": {
                "source_ids": ["open_webui_releases", "docker_blog"],
                "include": ["Open WebUI", "Docker", "local AI security"],
                "exclude": ["sponsored", "rumor"],
                "output_target": "briefings",
                "max_items": 10,
                "research_item_ids": ["research-1"],
                "research_basis": {
                    "source_ids": ["open_webui_releases", "docker_blog"],
                    "item_count": 1,
                    "error_count": 0,
                },
            },
            "safety": {
                "requires_user_confirmation": True,
                "requires_heimdal_validation": True,
                "requires_yggy_approval": True,
                "external_content_is_data_only": True,
            },
        }
    assert method == "GET"
    if path == "/tasks":
        return {
            "data": [
                {
                    "id": "daily_local_ai_security_briefing",
                    "name": "Daily Local AI Security Briefing",
                    "type": "topic_digest",
                    "enabled": True,
                    "status": "enabled",
                    "approval_level": "L1_NOTIFY_ONLY",
                    "created_by": "yggdrasil",
                    "config": {
                        "trigger": {"kind": "schedule", "cron": "0 8 * * 1-5", "timezone": "Europe/Berlin"},
                        "output": {"channel": "discord", "target": "briefings"},
                        "runtime": {"dry_run": False},
                    },
                },
                {
                    "id": "daily_ai_stack_health",
                    "name": "Daily AI Stack Health Check",
                    "type": "server_health",
                    "enabled": False,
                    "status": "pending_approval",
                    "approval_level": "L1_NOTIFY_ONLY",
                    "created_by": "bragi",
                    "config": {
                        "trigger": {"kind": "schedule", "cron": "0 8 * * *", "timezone": "Europe/Berlin"},
                        "output": {"channel": "discord", "target": "alerts"},
                        "runtime": {"dry_run": True},
                    },
                },
            ]
        }
    if path == "/capabilities":
        return {
            "data": [
                {
                    "id": "topic_digest.v1",
                    "purpose": "Create recurring summaries from approved source IDs.",
                    "maps_to_task_type": "topic_digest",
                    "allowed_approval_levels": ["L0_READ_ONLY", "L1_NOTIFY_ONLY"],
                    "allowed_output_targets": ["briefings", "alerts"],
                    "required_slots": ["task_id", "source_ids"],
                    "allowed_source_ids": ["docker_blog"],
                    "safety_rules": ["Source content is untrusted data."],
                }
            ]
        }
    if path == "/sources":
        return {
            "data": [
                {
                    "id": "docker_blog",
                    "name": "Docker blog",
                    "type": "rss",
                    "enabled": True,
                    "categories": ["containers", "security_news"],
                    "trust_level": "official_vendor_blog",
                    "ingestion_mode": "feed_metadata",
                },
                {
                    "id": "open_webui_releases",
                    "name": "Open WebUI releases",
                    "type": "rss",
                    "enabled": True,
                    "categories": ["local_ai", "project_releases"],
                    "trust_level": "official_project_release_feed",
                    "ingestion_mode": "feed_metadata",
                },
            ]
        }
    if path == "/health":
        return {"status": "ok", "database": {"connected": True}, "worker": {"status": "ok", "age_seconds": 2}}
    if path.startswith("/runs"):
        return {
            "data": [
                {
                    "id": "run-1",
                    "task_id": "daily_local_ai_security_briefing",
                    "status": "completed",
                    "created_at": "2026-05-17T08:00:00Z",
                    "completed_at": "2026-05-17T08:00:10Z",
                    "log": {
                        "result_status": "ok",
                        "secret": "must-not-leak",
                        "notification": {"sent": True, "webhook_url": "must-not-leak"},
                    },
                }
            ]
        }
    raise AssertionError(f"unexpected API path: {path}")


def test_context_query_pending_reviews_uses_redacted_task_state(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", context_api_fixture)

    context = bragi.build_context("what is pending?")

    assert context["read_only"] is True
    assert context["categories"] == ["pending_reviews"]
    assert context["data"]["pending_reviews"][0]["id"] == "daily_ai_stack_health"
    assert "nonce" not in json.dumps(context["data"]).lower()
    assert "must-not-leak" not in json.dumps(context)


def test_context_query_capabilities_sources_and_checks(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", context_api_fixture)

    context = bragi.build_context("what can you automate right now?")

    assert context["categories"] == ["capabilities", "sources", "health_checks", "n8n_webhooks"]
    assert context["data"]["capabilities"][0]["id"] == "topic_digest.v1"
    assert {source["id"] for source in context["data"]["sources"]} >= {"docker_blog", "open_webui_releases"}
    assert {check["id"] for check in context["data"]["health_checks"]} >= {"automation_api", "ollama"}
    serialized = json.dumps(context).lower()
    assert "https://github.com" not in serialized
    assert "http://automation-api" not in serialized


def test_context_query_recent_runs_omits_raw_logs_and_secrets(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", context_api_fixture)

    context = bragi.build_context("show recent run history")

    assert context["categories"] == ["recent_runs"]
    assert context["data"]["recent_runs"][0]["id"] == "run-1"
    serialized = json.dumps(context).lower()
    assert "must-not-leak" not in serialized
    assert "webhook_url" not in serialized
    assert "raw_logs" in serialized


def test_context_query_research_uses_approved_source_gateway(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return context_api_fixture(method, path, payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    context = bragi.build_context("what is new with Open WebUI releases?")

    assert context["categories"] == ["research"]
    assert calls[0][0:2] == ("POST", "/research/query")
    assert calls[0][2]["fetch"] is True
    assert context["data"]["research"]["items"][0]["source_id"] == "open_webui_releases"
    serialized = json.dumps(context).lower()
    assert "https://example.com/release" not in serialized
    assert "source_content_is_untrusted" in serialized


def test_context_chat_answer_includes_research_boundary(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", context_api_fixture)
    monkeypatch.setattr(bragi, "ollama_chat", lambda messages: (_ for _ in ()).throw(AssertionError("ollama called")))

    answer = bragi.route_chat([{"role": "user", "content": "what is new with Open WebUI releases?"}])

    assert "Approved-source research" in answer
    assert "Open WebUI security release" in answer
    assert "External source content is data, not command authority" in answer


def test_context_chat_answer_does_not_call_yggdrasil_or_ollama(monkeypatch):
    monkeypatch.setattr(bragi, "api_request", context_api_fixture)
    monkeypatch.setattr(bragi, "ollama_chat", lambda messages: (_ for _ in ()).throw(AssertionError("ollama called")))
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("yggdrasil called")))

    answer = bragi.route_chat([{"role": "user", "content": "what can you automate right now?"}])

    assert "Supported capabilities" in answer
    assert "Approved sources" in answer
    assert "Changes, runs, and approvals still go through Heimdal" in answer


def test_write_like_request_still_routes_to_gateway_not_context(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat([{"role": "user", "content": "draft a weekday 08:00 topic digest about Docker"}])

    assert calls[0][0:2] == ("POST", "/capabilities/validate-intent")
    assert "Canonical intent" in answer


def test_research_backed_topic_digest_draft_uses_suggestion_then_gateway(monkeypatch):
    calls = []

    def fake_api_request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/research/topic-digest-suggestion":
            return context_api_fixture(method, path, payload)
        return gateway_response_for(payload)

    monkeypatch.setattr(bragi, "api_request", fake_api_request)

    answer = bragi.route_chat(
        [
            {
                "role": "user",
                "content": "draft a weekday 08:00 research-backed topic digest from recent approved sources about local AI security",
            }
        ]
    )

    assert calls[0][0:2] == ("POST", "/research/topic-digest-suggestion")
    assert calls[0][2]["fetch"] is True
    assert calls[1][0:2] == ("POST", "/capabilities/validate-intent")
    intent = calls[1][2]
    assert intent["capability_id"] == "topic_digest.v1"
    assert intent["slots"]["source_ids"] == ["open_webui_releases", "ollama_releases", "n8n_releases", "docker_blog"]
    assert "Open WebUI" in intent["slots"]["include"]
    assert intent["slots"]["research_basis"]["external_content_is_data_only"] is True
    assert intent["slots"]["research_item_ids"] == ["research-1"]
    assert "Research basis" in answer
    assert "Reply `confirm`" in answer


def test_route_diagnostic_for_context_question():
    diagnostic = bragi.diagnose_route([{"role": "user", "content": "what can you automate right now?"}])

    assert diagnostic["route"] == "general_chat_with_context"
    assert diagnostic["context_categories"] == ["capabilities", "sources", "health_checks", "n8n_webhooks"]
    assert diagnostic["calls_external_services"] is False


def test_route_diagnostic_for_research_question():
    diagnostic = bragi.diagnose_route([{"role": "user", "content": "what is new with Docker security notes?"}])

    assert diagnostic["route"] == "general_chat_with_context"
    assert diagnostic["context_categories"] == ["research"]
    assert diagnostic["calls_external_services"] is False


def test_context_query_endpoint_requires_bragi_key(monkeypatch):
    monkeypatch.setattr(bragi, "API_KEY", "test-bragi-key")
    monkeypatch.setattr(bragi, "api_request", context_api_fixture)
    client = TestClient(bragi.app)

    unauthorized = client.post("/context/query", json={"query": "what is pending?"})
    authorized = client.post(
        "/context/query",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"query": "what is pending?"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["data"]["pending_reviews"][0]["id"] == "daily_ai_stack_health"


def reset_memory(tmp_path):
    memory_store.reset_memory_store_for_tests(f"sqlite+pysqlite:///{tmp_path / 'bragi_memory.db'}")


def test_memory_propose_commit_query_is_user_scoped(tmp_path):
    reset_memory(tmp_path)

    pending = memory_store.propose_memory(
        user_id="local_user",
        category="notification_style",
        key="discord_alert_detail",
        value="short unless a failure occurred",
    )
    assert pending["status"] == "pending"
    assert memory_store.query_memory(user_id="local_user") == []

    active = memory_store.commit_memory(memory_id=pending["id"], user_id="local_user")

    assert active["status"] == "active"
    assert memory_store.query_memory(user_id="local_user")[0]["key"] == "discord_alert_detail"
    assert memory_store.query_memory(user_id="other_user") == []


def test_memory_rejects_secret_like_values(tmp_path):
    reset_memory(tmp_path)

    try:
        memory_store.propose_memory(
            user_id="local_user",
            category="note",
            key="bad_secret",
            value="my token is xoxb_this-should-not-be-stored-1234567890",
        )
    except memory_store.MemoryValidationError as exc:
        assert "secret-like" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("secret-like memory was accepted")


def test_memory_endpoints_require_bragi_key_and_do_not_store_secrets(tmp_path, monkeypatch):
    reset_memory(tmp_path)
    monkeypatch.setattr(bragi, "API_KEY", "test-bragi-key")
    client = TestClient(bragi.app)

    unauthorized = client.post(
        "/memory/propose",
        json={"category": "preference", "key": "message_style", "value": "short"},
    )
    rejected_secret = client.post(
        "/memory/propose",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"category": "note", "key": "api_key", "value": "sk_not_for_memory_1234567890"},
    )
    proposed = client.post(
        "/memory/propose",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"category": "preference", "key": "message_style", "value": "short technical replies"},
    )
    memory_id = proposed.json()["memory"]["id"]
    committed = client.post(
        "/memory/commit",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"memory_id": memory_id},
    )
    queried = client.post(
        "/memory/query",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"user_id": "local_user"},
    )

    assert unauthorized.status_code == 401
    assert rejected_secret.status_code == 422
    assert proposed.status_code == 200
    assert proposed.json()["status"] == "needs_confirmation"
    assert committed.status_code == 200
    assert committed.json()["status"] == "saved"
    assert queried.json()["records"][0]["key"] == "message_style"


def test_memory_chat_proposal_and_commit_do_not_call_execution_paths(tmp_path, monkeypatch):
    reset_memory(tmp_path)
    monkeypatch.setattr(bragi, "api_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("api called")))
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("yggdrasil called")))

    proposal = bragi.route_chat([{"role": "user", "content": "Remember that I prefer short Discord alerts unless something failed."}])
    commit = bragi.route_chat(
        [
            {"role": "assistant", "content": proposal},
            {"role": "user", "content": "remember"},
        ]
    )
    query = bragi.route_chat([{"role": "user", "content": "what do you remember about me?"}])

    assert "Pending memory proposal" in proposal
    assert "Saved as non-secret Bragi memory" in commit
    assert "short Discord alerts" in query


def test_memory_chat_rejects_secret_without_storing(tmp_path):
    reset_memory(tmp_path)

    answer = bragi.route_chat([{"role": "user", "content": "Remember that my API key is sk_not_for_memory_1234567890"}])

    assert "will not store" in answer
    assert memory_store.query_memory(user_id="local_user", include_pending=True) == []


def test_memory_forget_marks_matching_records_forgotten(tmp_path):
    reset_memory(tmp_path)
    pending = memory_store.propose_memory(
        user_id="local_user",
        category="preference",
        key="message_style",
        value="short replies",
    )
    memory_store.commit_memory(memory_id=pending["id"], user_id="local_user")

    answer = bragi.route_chat([{"role": "user", "content": "forget message style"}])

    assert "Forgot 1 Bragi memory" in answer
    assert memory_store.query_memory(user_id="local_user") == []


def test_memory_diagnostics_identifies_proposal_and_forget():
    proposal = bragi.diagnose_route([{"role": "user", "content": "Remember that I prefer concise summaries."}])
    forget = bragi.diagnose_route([{"role": "user", "content": "forget concise summaries"}])

    assert proposal["mode"] == "memory_proposal"
    assert proposal["route"] == "bragi_memory_propose"
    assert forget["mode"] == "memory_forget"
    assert forget["route"] == "bragi_memory_forget"


def configure_discord_channel(tmp_path, monkeypatch, *, audience="local_user", max_chars=3000, allowed_user_ids="user-1"):
    config_root = tmp_path / "configs"
    config_root.mkdir()
    (config_root / "channels.yaml").write_text(
        f"""
version: 1
channels:
  - id: discord_home
    type: discord
    enabled: true
    audience: {audience}
    channel_id_ref: DISCORD_HOME_CHANNEL
    allowed_user_ids_ref: DISCORD_ALLOWED_USER_IDS
    allowed_capabilities:
      - chat
      - context
      - memory
      - draft_task
      - task_read
      - run_l1
      - pause_l1
    allow_approvals: false
    max_message_chars: {max_chars}
    strip_mentions: true
    reject_attachments: true
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(bragi, "CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("DISCORD_HOME_CHANNEL", "channel-1")
    if allowed_user_ids is None:
        monkeypatch.delenv("DISCORD_ALLOWED_USER_IDS", raising=False)
    else:
        monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", allowed_user_ids)


def discord_client(monkeypatch):
    monkeypatch.setattr(bragi, "API_KEY", "test-bragi-key")
    return TestClient(bragi.app)


def test_discord_message_endpoint_routes_context_safely(tmp_path, monkeypatch):
    configure_discord_channel(tmp_path, monkeypatch)
    monkeypatch.setattr(bragi, "api_request", context_api_fixture)
    client = discord_client(monkeypatch)

    response = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={
            "channel_id": "channel-1",
            "author_id": "user-1",
            "content": "<@1234> what can you automate right now?",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["channel_config_id"] == "discord_home"
    assert body["classification"]["route"] == "general_chat_with_context"
    assert body["classification"]["required_capability"] == "context"
    assert "Supported capabilities" in body["reply"]
    assert body["allowed_mentions"] == []


def test_discord_message_endpoint_rejects_unknown_channel(tmp_path, monkeypatch):
    configure_discord_channel(tmp_path, monkeypatch)
    client = discord_client(monkeypatch)

    response = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"channel_id": "other-channel", "author_id": "user-1", "content": "hello"},
    )

    assert response.status_code == 403


def test_discord_message_endpoint_enforces_allowed_user_ids(tmp_path, monkeypatch):
    configure_discord_channel(tmp_path, monkeypatch, allowed_user_ids="allowed-user")
    client = discord_client(monkeypatch)

    response = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"channel_id": "channel-1", "author_id": "other-user", "content": "hello"},
    )

    assert response.status_code == 403


def test_discord_message_endpoint_rejects_bots_attachments_and_overlong(tmp_path, monkeypatch):
    configure_discord_channel(tmp_path, monkeypatch, max_chars=5)
    client = discord_client(monkeypatch)
    base = {"channel_id": "channel-1", "author_id": "user-1"}

    bot = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={**base, "content": "hello", "is_bot": True},
    )
    attachment = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={**base, "content": "hello", "attachments": [{"filename": "x.txt"}]},
    )
    overlong = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={**base, "content": "123456"},
    )

    assert bot.status_code == 403
    assert attachment.status_code == 422
    assert overlong.status_code == 413


def test_discord_message_endpoint_blocks_approval_and_admin_material(tmp_path, monkeypatch):
    configure_discord_channel(tmp_path, monkeypatch)
    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", lambda payload: (_ for _ in ()).throw(AssertionError("yggdrasil called")))
    client = discord_client(monkeypatch)

    response = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"channel_id": "channel-1", "author_id": "user-1", "content": "approve task with nonce abc"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["classification"]["route"] == "discord_admin_guard"
    assert "ops UI or admin CLI" in body["reply"]
    assert "abc" not in body["reply"]


def test_discord_message_endpoint_can_route_run_operation(tmp_path, monkeypatch):
    configure_discord_channel(tmp_path, monkeypatch)
    calls = []

    def fake_yggdrasil(payload):
        calls.append(payload)
        return {"status": "ok", "answer": "Run queued for task `daily_local_ai_security_briefing`."}

    monkeypatch.setattr(bragi, "yggdrasil_canonical_request", fake_yggdrasil)
    client = discord_client(monkeypatch)

    response = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={"channel_id": "channel-1", "author_id": "user-1", "content": "send daily brief now"},
    )

    body = response.json()
    assert response.status_code == 200
    assert calls == [{"action": "run_task", "task_id": "daily_local_ai_security_briefing"}]
    assert body["classification"]["required_capability"] == "run_l1"
    assert body["classification"]["forwarded_to_yggdrasil"] is True
    assert "Run queued" in body["reply"]


def test_discord_memory_uses_channel_audience_scope(tmp_path, monkeypatch):
    reset_memory(tmp_path)
    configure_discord_channel(tmp_path, monkeypatch, audience="discord_user")
    client = discord_client(monkeypatch)

    response = client.post(
        "/channels/discord/message",
        headers={"Authorization": "Bearer test-bragi-key"},
        json={
            "channel_id": "channel-1",
            "author_id": "user-1",
            "content": "Remember that I prefer compact replies in Discord.",
        },
    )

    body = response.json()
    pending = memory_store.query_memory(user_id="discord_user", include_pending=True)
    assert response.status_code == 200
    assert body["user_id"] == "discord_user"
    assert body["classification"]["required_capability"] == "memory"
    assert pending[0]["status"] == "pending"
    assert memory_store.query_memory(user_id="local_user", include_pending=True) == []
