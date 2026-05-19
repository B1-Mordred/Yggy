from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "n8n" / "workflows" / "daily_briefing_webhook_stub.json"


def load_workflow() -> dict:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_n8n_stub_webhook_uses_header_auth_credential_reference():
    workflow = load_workflow()
    webhook = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.webhook")

    assert webhook["parameters"]["path"] == "yggy-daily-briefing"
    assert webhook["parameters"]["httpMethod"] == "POST"
    assert webhook["parameters"]["authentication"] == "headerAuth"
    assert webhook["webhookId"] == "yggy-daily-briefing"
    assert webhook["credentials"]["httpHeaderAuth"] == {
        "id": "yggyWebhookHeaderAuth01",
        "name": "Yggy Webhook Header Auth",
    }


def test_n8n_stub_workflow_does_not_embed_secret_material():
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "N8N_WEBHOOK_SHARED_SECRET" not in raw
    assert "X-Yggy-Webhook-Token" not in raw
    assert "$env" not in raw
    assert "wrong-token" not in raw
    assert "test-shared-secret" not in raw


def test_n8n_stub_connects_webhook_only_to_normalizer_response():
    workflow = load_workflow()
    connections = workflow["connections"]["Yggy Daily Briefing Webhook"]["main"][0]

    assert connections == [{"node": "Normalize Digest Payload", "type": "main", "index": 0}]
    assert not any(node["type"] == "n8n-nodes-base.if" for node in workflow["nodes"])


def test_n8n_stub_response_normalizes_digest_payload_without_headers():
    workflow = load_workflow()
    response = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.respondToWebhook")
    body = response["parameters"]["responseBody"]

    assert response["name"] == "Normalize Digest Payload"
    assert "normalize_digest_payload" in body
    assert "$json.body.task_id" in body
    assert "$json.body.payload" in body
    assert "payload_keys" in body
    assert "headers" not in body
