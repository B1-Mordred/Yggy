from __future__ import annotations

from conftest import TOOL_HEADERS, sample_task


def test_tool_key_can_create_disabled_l1_draft(client):
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("draft_l1"))
    assert response.status_code == 201
    body = response.json()
    assert body["task"]["enabled"] is False
    assert body["task"]["status"] == "pending_approval"
    assert body["approval"]["approval_level"] == "L1_NOTIFY_ONLY"
    assert body["approval"]["created_at"] is not None


def test_tool_key_can_create_disabled_l0_draft(client):
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("draft_l0", "L0_READ_ONLY"))
    assert response.status_code == 201
    body = response.json()
    assert body["task"]["enabled"] is False
    assert body["task"]["status"] == "draft"
    assert body["approval"] is None


def test_allow_shell_is_rejected(client):
    task = sample_task("bad_shell", policy={"allow_shell": True})
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "allow_shell" in response.text


def test_allow_docker_socket_is_rejected(client):
    task = sample_task("bad_docker", policy={"allow_docker_socket": True})
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "allow_docker_socket" in response.text


def test_non_whitelisted_discord_target_is_rejected(client):
    task = sample_task("bad_discord", output={"target": "random"})
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "not whitelisted" in response.text


def test_external_side_effect_below_l3_is_rejected(client):
    task = sample_task("bad_external", policy={"allow_external_side_effects": True})
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "external side effects require L3" in response.text


def test_topic_digest_web_query_is_rejected_by_source_policy(client):
    task = sample_task(
        "bad_web_query",
        sources=[{"type": "web_query", "query": "Open WebUI Ollama security"}],
    )
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "web_query sources are disabled" in response.text


def test_topic_digest_unapproved_rss_is_rejected(client):
    task = sample_task(
        "bad_source",
        sources=[{"source_id": "unknown_source", "type": "rss", "url": "https://example.com/feed.xml"}],
    )
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "not in the approved source registry" in response.text


def test_topic_digest_source_id_must_match_registry_entry(client):
    task = sample_task(
        "bad_source_identity",
        sources=[
            {
                "source_id": "open_webui_releases",
                "type": "rss",
                "url": "https://github.com/ollama/ollama/releases.atom",
            }
        ],
    )
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "does not match the configured source identity" in response.text


def n8n_task(task_id: str, **overrides):
    task = sample_task(
        task_id,
        type="n8n_webhook",
        sources=[],
        filters={"include": [], "exclude": []},
        output={"channel": "internal", "target": "n8n", "format": "bounded webhook dispatch status"},
        policy={"require_sources": False},
    )
    task["n8n"] = {
        "webhook_id": "daily_briefing_stub",
        "path": "/webhook/yggy-daily-briefing",
        "method": "POST",
        "payload": {"purpose": "test"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(task.get(key), dict):
            task[key].update(value)
        else:
            task[key] = value
    return task


def test_approved_n8n_webhook_task_is_accepted(client):
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=n8n_task("n8n_ok"))
    assert response.status_code == 201
    assert response.json()["task"]["config"]["n8n"]["webhook_id"] == "daily_briefing_stub"


def test_n8n_webhook_unapproved_id_is_rejected(client):
    task = n8n_task("bad_n8n_id", n8n={"webhook_id": "unknown_webhook"})
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "not in the approved registry" in response.text


def test_n8n_webhook_path_must_match_registry(client):
    task = n8n_task("bad_n8n_path", n8n={"path": "/webhook/yggy/other"})
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "does not match the configured webhook path" in response.text
