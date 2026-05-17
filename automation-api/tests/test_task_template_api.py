from __future__ import annotations

from conftest import ADMIN_HEADERS, TOOL_HEADERS


def test_tool_key_can_list_task_templates(client):
    response = client.get("/task-templates", headers=TOOL_HEADERS)

    assert response.status_code == 200
    template_ids = {item["id"] for item in response.json()}
    assert {"topic_digest", "server_health", "backup_verification", "n8n_webhook"} <= template_ids


def test_tool_key_can_get_task_template_detail(client):
    response = client.get("/task-templates/topic_digest", headers=TOOL_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "topic_digest"
    assert data["default_approval_level"] == "L1_NOTIFY_ONLY"
    assert data["default_source_ids"] == ["open_webui_releases", "ollama_releases", "n8n_releases", "docker_blog"]
    assert data["defaults"]["runtime"]["dry_run"] is True


def test_tool_key_can_create_disabled_draft_from_template(client):
    response = client.post(
        "/task-templates/topic_digest/draft",
        headers=TOOL_HEADERS,
        json={
            "id": "template_api_digest",
            "name": "Template API Digest",
            "cron": "0 8 * * 1-5",
            "output_target": "briefings",
            "source_ids": ["open_webui_releases", "ollama_releases"],
            "include": ["Open WebUI", "Ollama"],
            "exclude": ["sponsored"],
        },
    )

    assert response.status_code == 201
    data = response.json()
    task = data["task"]
    rendered = data["rendered_config"]
    assert data["template"]["id"] == "topic_digest"
    assert data["approval"]["approval_level"] == "L1_NOTIFY_ONLY"
    assert task["id"] == "template_api_digest"
    assert task["enabled"] is False
    assert rendered["enabled"] is False
    assert rendered["runtime"]["dry_run"] is True
    assert rendered["policy"]["allow_shell"] is False
    assert rendered["policy"]["allow_docker_socket"] is False
    assert [source["source_id"] for source in rendered["sources"]] == ["open_webui_releases", "ollama_releases"]


def test_admin_key_can_create_disabled_draft_from_template(client):
    response = client.post(
        "/task-templates/server_health/draft",
        headers=ADMIN_HEADERS,
        json={
            "id": "template_api_server_health",
            "name": "Template API Server Health",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["task"]["type"] == "server_health"
    assert data["rendered_config"]["enabled"] is False
    assert data["rendered_config"]["runtime"]["dry_run"] is True


def test_server_health_template_accepts_approved_check_ids(client):
    response = client.post(
        "/task-templates/server_health/draft",
        headers=TOOL_HEADERS,
        json={
            "id": "template_api_selected_health",
            "name": "Selected Health Checks",
            "check_ids": ["automation_api", "automation_worker", "n8n"],
        },
    )

    assert response.status_code == 201
    checks = response.json()["rendered_config"]["checks"]
    assert [check["name"] for check in checks] == ["automation_api", "automation_worker", "n8n"]
    assert all(check["url"].startswith("http://") for check in checks)


def test_n8n_webhook_template_accepts_approved_webhook_id(client):
    response = client.post(
        "/task-templates/n8n_webhook/draft",
        headers=TOOL_HEADERS,
        json={
            "id": "template_api_n8n_selected",
            "name": "Selected n8n Webhook",
            "webhook_id": "daily_briefing_stub",
            "n8n_payload": {"description": "bounded test payload"},
        },
    )

    assert response.status_code == 201
    n8n = response.json()["rendered_config"]["n8n"]
    assert n8n["webhook_id"] == "daily_briefing_stub"
    assert n8n["path"] == "/webhook/yggy-daily-briefing"
    assert n8n["payload"] == {"description": "bounded test payload"}


def test_template_draft_unknown_template_returns_404(client):
    response = client.post(
        "/task-templates/not_a_template/draft",
        headers=TOOL_HEADERS,
        json={"id": "unknown_template_task", "name": "Unknown Template Task"},
    )

    assert response.status_code == 404


def test_template_draft_rejects_bad_output_target(client):
    response = client.post(
        "/task-templates/server_health/draft",
        headers=TOOL_HEADERS,
        json={
            "id": "bad_template_output_target",
            "name": "Bad Template Output Target",
            "output_target": "briefings",
        },
    )

    assert response.status_code == 422
    assert "not allowed" in response.text


def test_template_draft_rejects_dry_run_field(client):
    response = client.post(
        "/task-templates/topic_digest/draft",
        headers=TOOL_HEADERS,
        json={
            "id": "bad_template_dry_run",
            "name": "Bad Template Dry Run",
            "dry_run": False,
        },
    )

    assert response.status_code == 422
    assert "extra_forbidden" in response.text


def test_template_draft_rejects_unknown_source(client):
    response = client.post(
        "/task-templates/topic_digest/draft",
        headers=TOOL_HEADERS,
        json={
            "id": "bad_template_source",
            "name": "Bad Template Source",
            "source_ids": ["not_registered"],
        },
    )

    assert response.status_code == 422
    assert "not enabled in approved_sources.yaml" in response.text
