from __future__ import annotations

from conftest import ADMIN_HEADERS, TOOL_HEADERS, sample_task


def test_tool_key_cannot_approve_l2_and_admin_can_with_nonce(client):
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("needs_l2", "L2_LOCAL_WRITE"))
    assert create_response.status_code == 201
    approval = create_response.json()["approval"]

    tool_response = client.post(
        f"/approvals/{approval['id']}/approve",
        headers=TOOL_HEADERS,
        json={"nonce": approval["nonce"]},
    )
    assert tool_response.status_code == 403

    admin_response = client.post(
        f"/approvals/{approval['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"nonce": approval["nonce"]},
    )
    assert admin_response.status_code == 200
    assert admin_response.json()["status"] == "approved"

    task_response = client.get("/tasks/needs_l2", headers=ADMIN_HEADERS)
    assert task_response.json()["enabled"] is True


def test_invalid_nonce_fails(client):
    create_response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("bad_nonce", "L2_LOCAL_WRITE"))
    approval = create_response.json()["approval"]
    response = client.post(
        f"/approvals/{approval['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"nonce": "wrong"},
    )
    assert response.status_code == 403
    assert "invalid nonce" in response.text
