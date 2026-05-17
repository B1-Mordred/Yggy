from __future__ import annotations

from conftest import ADMIN_HEADERS, TOOL_HEADERS


def server_health_intent(**overrides):
    intent = {
        "intent": "draft_task",
        "capability_id": "server_health.v1",
        "confidence": 0.93,
        "requires_user_confirmation": True,
        "user_confirmation_obtained": True,
        "user_request": "Watch the AI stack and alert me if something breaks.",
        "slots": {
            "task_id": "daily_ai_stack_health",
            "name": "Daily AI Stack Health Check",
            "cron": "0 8 * * *",
            "timezone": "Europe/Berlin",
            "check_ids": ["open_webui", "ollama", "automation_api", "automation_worker", "n8n"],
            "output_target": "alerts",
        },
    }
    for key, value in overrides.items():
        if key == "slots":
            intent["slots"].update(value)
        else:
            intent[key] = value
    return intent


def topic_digest_intent(**overrides):
    intent = {
        "intent": "draft_task",
        "capability_id": "topic_digest.v1",
        "confidence": 0.91,
        "requires_user_confirmation": True,
        "user_confirmation_obtained": True,
        "slots": {
            "task_id": "daily_local_ai_security_briefing",
            "name": "Daily Local AI Security Briefing",
            "cron": "0 8 * * 1-5",
            "timezone": "Europe/Berlin",
            "source_ids": ["open_webui_releases", "ollama_releases"],
            "include": ["Open WebUI", "Ollama"],
            "exclude": ["sponsored"],
            "output_target": "briefings",
        },
    }
    for key, value in overrides.items():
        if key == "slots":
            intent["slots"].update(value)
        else:
            intent[key] = value
    return intent


def n8n_intent(**overrides):
    intent = {
        "intent": "draft_task",
        "capability_id": "n8n_webhook.v1",
        "confidence": 0.91,
        "requires_user_confirmation": True,
        "user_confirmation_obtained": True,
        "slots": {
            "task_id": "daily_briefing_n8n_stub",
            "name": "Daily Briefing n8n Payload Normalizer",
            "cron": "15 8 * * 1-5",
            "timezone": "Europe/Berlin",
            "webhook_id": "daily_briefing_stub",
            "output_target": "n8n",
            "payload_description": "Normalize the approved daily briefing payload.",
        },
    }
    for key, value in overrides.items():
        if key == "slots":
            intent["slots"].update(value)
        else:
            intent[key] = value
    return intent


def test_tool_key_can_list_capabilities(client):
    response = client.get("/capabilities", headers=TOOL_HEADERS)

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert {"server_health.v1", "topic_digest.v1", "n8n_webhook.v1"} <= ids
    assert "unsafe_keywords" not in response.text


def test_gateway_accepts_supported_intents(client):
    for payload in [server_health_intent(), topic_digest_intent(), n8n_intent()]:
        response = client.post("/capabilities/prepare-yggdrasil-request", headers=TOOL_HEADERS, json=payload)
        body = response.json()

        assert response.status_code == 200
        assert body["outcome"] == "ACCEPT"
        assert body["yggdrasil_request"]["action"] == "draft_task_from_template"
        assert body["yggdrasil_request"]["template_id"]
        assert body["confirmation_summary"]["dry_run"] is True


def test_gateway_requires_missing_slots_and_user_confirmation(client):
    missing = server_health_intent(slots={"check_ids": []})
    unconfirmed = server_health_intent(user_confirmation_obtained=False)

    missing_response = client.post("/capabilities/validate-intent", headers=TOOL_HEADERS, json=missing)
    unconfirmed_response = client.post("/capabilities/validate-intent", headers=TOOL_HEADERS, json=unconfirmed)

    assert missing_response.json()["outcome"] == "ASK_CLARIFICATION"
    assert "check_ids" in missing_response.json()["missing_slots"]
    assert unconfirmed_response.json()["outcome"] == "ASK_CLARIFICATION"
    assert "user_confirmation" in unconfirmed_response.json()["missing_slots"]


def test_gateway_rejects_unknown_and_unsafe_requests(client):
    unknown = server_health_intent(capability_id="printer_toner.v1")
    unsafe = server_health_intent(user_request="Restart Docker whenever something looks wrong.")

    unknown_response = client.post("/capabilities/validate-intent", headers=TOOL_HEADERS, json=unknown)
    unsafe_response = client.post("/capabilities/validate-intent", headers=TOOL_HEADERS, json=unsafe)

    assert unknown_response.json()["outcome"] == "REJECT_UNSUPPORTED"
    assert unsafe_response.json()["outcome"] == "REJECT_UNSAFE"
    assert "restart docker" in unsafe_response.text.lower()


def test_gateway_proposes_new_capability_for_printer_toner(client):
    payload = server_health_intent(user_request="Check my printer toner and warn me before it runs out.")

    response = client.post("/capabilities/validate-intent", headers=TOOL_HEADERS, json=payload)

    assert response.status_code == 200
    assert response.json()["outcome"] == "PROPOSE_NEW_CAPABILITY"


def test_gateway_rejects_unapproved_sources_webhooks_and_broad_web_queries(client):
    bad_source = topic_digest_intent(slots={"source_ids": ["not_registered"]})
    web_query = topic_digest_intent(slots={"web_query": "Open WebUI security"})
    bad_webhook = n8n_intent(slots={"webhook_id": "unknown_webhook"})

    bad_source_response = client.post("/capabilities/validate-intent", headers=TOOL_HEADERS, json=bad_source)
    web_query_response = client.post("/capabilities/validate-intent", headers=TOOL_HEADERS, json=web_query)
    bad_webhook_response = client.post("/capabilities/validate-intent", headers=ADMIN_HEADERS, json=bad_webhook)

    assert bad_source_response.json()["outcome"] == "REJECT_UNSAFE"
    assert web_query_response.json()["outcome"] == "REJECT_UNSAFE"
    assert bad_webhook_response.json()["outcome"] == "REJECT_UNSAFE"
