from __future__ import annotations

from conftest import ADMIN_HEADERS, TOOL_HEADERS, sample_task


def create_task(client, task_id: str = "changeable_task") -> dict:
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task(task_id))
    assert response.status_code == 201
    return response.json()["task"]


def proposal_payload(task_id: str, *, cron: str = "30 7 * * 1-5") -> dict:
    proposed = sample_task(task_id)
    proposed["trigger"]["cron"] = cron
    proposed["filters"]["include"] = ["Open WebUI", "Ollama", "security"]
    return {
        "requested_by": "yggdrasil",
        "summary": "Move the weekday digest earlier and add security filter.",
        "proposed_config": proposed,
    }


def test_tool_can_create_task_change_proposal_without_mutating_task(client):
    create_task(client, "proposal_task")

    response = client.post(
        "/tasks/proposal_task/propose-change",
        headers=TOOL_HEADERS,
        json=proposal_payload("proposal_task"),
    )

    assert response.status_code == 201
    proposal = response.json()
    assert proposal["task_id"] == "proposal_task"
    assert proposal["status"] == "pending"
    assert proposal["nonce"]
    assert proposal["diff"]["counts"]["changed"] >= 1
    assert "schedule" in proposal["risk"]["categories"]

    task_response = client.get("/tasks/proposal_task", headers=TOOL_HEADERS)
    assert task_response.json()["config"]["trigger"]["cron"] == "0 8 * * 1-5"


def test_tool_cannot_approve_or_apply_task_change_proposal(client):
    create_task(client, "tool_cannot_apply")
    proposal = client.post(
        "/tasks/tool_cannot_apply/propose-change",
        headers=TOOL_HEADERS,
        json=proposal_payload("tool_cannot_apply"),
    ).json()

    approve_response = client.post(
        f"/task-change-proposals/{proposal['id']}/approve",
        headers=TOOL_HEADERS,
        json={"nonce": proposal["nonce"]},
    )
    apply_response = client.post(
        f"/task-change-proposals/{proposal['id']}/apply",
        headers=TOOL_HEADERS,
    )

    assert approve_response.status_code == 403
    assert apply_response.status_code == 403


def test_admin_approve_and_apply_task_change_proposal(client):
    create_task(client, "admin_apply_proposal")
    proposal = client.post(
        "/tasks/admin_apply_proposal/propose-change",
        headers=TOOL_HEADERS,
        json=proposal_payload("admin_apply_proposal", cron="45 6 * * 1-5"),
    ).json()

    bad_nonce = client.post(
        f"/task-change-proposals/{proposal['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"nonce": "wrong"},
    )
    assert bad_nonce.status_code == 403

    approve_response = client.post(
        f"/task-change-proposals/{proposal['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"nonce": proposal["nonce"]},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"

    apply_response = client.post(
        f"/task-change-proposals/{proposal['id']}/apply",
        headers=ADMIN_HEADERS,
    )
    assert apply_response.status_code == 200
    applied = apply_response.json()
    assert applied["proposal"]["status"] == "applied"
    assert applied["task"]["config"]["trigger"]["cron"] == "45 6 * * 1-5"


def test_apply_rejects_when_base_task_changed(client):
    create_task(client, "stale_proposal_task")
    proposal = client.post(
        "/tasks/stale_proposal_task/propose-change",
        headers=TOOL_HEADERS,
        json=proposal_payload("stale_proposal_task", cron="15 6 * * 1-5"),
    ).json()

    direct_update = sample_task("stale_proposal_task")
    direct_update["trigger"]["cron"] = "0 9 * * 1-5"
    response = client.put("/tasks/stale_proposal_task", headers=ADMIN_HEADERS, json=direct_update)
    assert response.status_code == 200

    approve_response = client.post(
        f"/task-change-proposals/{proposal['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"nonce": proposal["nonce"]},
    )
    assert approve_response.status_code == 200

    apply_response = client.post(
        f"/task-change-proposals/{proposal['id']}/apply",
        headers=ADMIN_HEADERS,
    )
    assert apply_response.status_code == 409
    assert "changed since proposal was created" in apply_response.text


def test_noop_task_change_proposal_is_rejected(client):
    create_task(client, "noop_proposal_task")

    response = client.post(
        "/tasks/noop_proposal_task/propose-change",
        headers=TOOL_HEADERS,
        json={"proposed_config": sample_task("noop_proposal_task")},
    )

    assert response.status_code == 422
    assert "does not change" in response.text


def test_task_change_proposal_rejects_mismatched_task_id(client):
    create_task(client, "proposal_id_task")
    response = client.post(
        "/tasks/proposal_id_task/propose-change",
        headers=TOOL_HEADERS,
        json=proposal_payload("different_task_id"),
    )

    assert response.status_code == 422
    assert "must match task_id" in response.text


def test_admin_can_reject_task_change_proposal(client):
    create_task(client, "reject_proposal_task")
    proposal = client.post(
        "/tasks/reject_proposal_task/propose-change",
        headers=TOOL_HEADERS,
        json=proposal_payload("reject_proposal_task"),
    ).json()

    response = client.post(
        f"/task-change-proposals/{proposal['id']}/reject",
        headers=ADMIN_HEADERS,
        json={"reason": "not needed"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_list_and_get_task_change_proposals(client):
    create_task(client, "list_proposal_task")
    proposal = client.post(
        "/tasks/list_proposal_task/propose-change",
        headers=TOOL_HEADERS,
        json=proposal_payload("list_proposal_task"),
    ).json()

    list_response = client.get("/task-change-proposals?task_id=list_proposal_task", headers=TOOL_HEADERS)
    detail_response = client.get(f"/task-change-proposals/{proposal['id']}", headers=TOOL_HEADERS)

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [proposal["id"]]
    assert detail_response.status_code == 200
    assert detail_response.json()["proposed_config"]["trigger"]["cron"] == "30 7 * * 1-5"
