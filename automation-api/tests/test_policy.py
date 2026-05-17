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


def test_topic_digest_can_use_included_preapproved_source(client):
    task = sample_task(
        "included_source_ok",
        sources=[
            {
                "source_id": "cisa_news_events",
                "type": "http",
                "url": "https://www.cisa.gov/news-events",
            }
        ],
    )
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)

    assert response.status_code == 201
    assert response.json()["task"]["config"]["sources"][0]["source_id"] == "cisa_news_events"


def test_topic_digest_disabled_source_is_rejected(client, tmp_path, monkeypatch):
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        """
version: 1
sources:
  - id: disabled_feed
    name: Disabled feed
    type: rss
    url: https://example.com/feed.xml
    categories:
      - local_ai
    trust_level: approved_test_feed
    enabled: false
    max_items: 5
""",
        encoding="utf-8",
    )
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        f"""
version: 1
allowed_discord_targets:
  - briefings
approval_thresholds:
  auto_allow:
    - L0_READ_ONLY
  initial_approval_required:
    - L1_NOTIFY_ONLY
  admin_required:
    - L2_LOCAL_WRITE
    - L3_EXTERNAL_SIDE_EFFECT
  manual_only:
    - L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE
source_policy:
  approved_sources_file: {sources}
  require_approved_sources_for_task_types:
    - topic_digest
  require_source_ids: true
  allow_web_query_sources: false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOMATION_POLICY_FILE", str(policy))

    task = sample_task(
        "disabled_source",
        sources=[{"source_id": "disabled_feed", "type": "rss", "url": "https://example.com/feed.xml"}],
    )
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)

    assert response.status_code == 422
    assert "disabled in the approved source registry" in response.text


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


def test_topic_digest_optional_n8n_webhook_is_validated(client):
    task = sample_task(
        "topic_with_n8n",
        n8n={
            "webhook_id": "daily_briefing_stub",
            "path": "/webhook/yggy-daily-briefing",
            "method": "POST",
            "payload": {"purpose": "daily_briefing_payload_normalizer"},
        },
    )
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 201
    assert response.json()["task"]["config"]["n8n"]["webhook_id"] == "daily_briefing_stub"


def test_topic_digest_optional_n8n_webhook_rejects_unapproved_path(client):
    task = sample_task(
        "topic_bad_n8n_path",
        n8n={
            "webhook_id": "daily_briefing_stub",
            "path": "/webhook/not-approved",
            "method": "POST",
            "payload": {"purpose": "daily_briefing_payload_normalizer"},
        },
    )
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=task)
    assert response.status_code == 422
    assert "does not match the configured webhook path" in response.text
