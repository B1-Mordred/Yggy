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
