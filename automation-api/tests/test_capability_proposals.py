from __future__ import annotations

from conftest import ADMIN_HEADERS, TOOL_HEADERS


def capability_proposal_payload(**overrides):
    payload = {
        "title": "Printer Supply Monitoring",
        "requested_by": "bragi",
        "source_channel": "discord",
        "original_request_preview": "Check my printer toner and warn me before it runs out.",
        "purpose": "Monitor approved printer supply status and notify before toner or ink levels become low.",
        "suggested_capability_id": "printer_supply_snmp.v1",
        "suggested_task_type": "printer_supply_snmp",
        "likely_approval_level": "L1_NOTIFY_ONLY",
        "required_inputs": ["approved printer ID", "polling schedule", "low-supply threshold"],
        "safety_rules": ["must not scan the LAN", "must not change printer configuration"],
        "non_goals": ["no arbitrary shell execution", "no printer administration changes"],
        "review_notes": "Useful but unsupported.",
    }
    payload.update(overrides)
    return payload


def test_tool_can_draft_capability_proposal_without_task_or_approval(client):
    response = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(
            implementation_spec={
                "archetype": "monitoring_check",
                "worker_contract": ["read-only supply check", "no printer configuration writes"],
            }
        ),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["suggested_capability_id"] == "printer_supply_snmp.v1"
    assert body["implementation_spec"]["archetype"] == "monitoring_check"
    assert "read-only supply check" in body["implementation_spec"]["worker_contract"]
    assert body["execution"] == {"creates_task": False, "creates_approval": False, "can_be_applied": False}
    assert "nonce" not in response.text.lower()

    listed = client.get("/capability-proposals?status=pending", headers=TOOL_HEADERS)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]

    tasks = client.get("/tasks", headers=TOOL_HEADERS)
    approvals = client.get("/approvals", headers=ADMIN_HEADERS)
    assert tasks.status_code == 200
    assert approvals.status_code == 200
    assert tasks.json() == []
    assert approvals.json() == []


def test_implementation_plan_contains_compiled_stages_and_deploy_gate(client):
    proposal = client.post("/capability-proposals/draft", headers=TOOL_HEADERS, json=capability_proposal_payload()).json()
    accepted = client.post(f"/capability-proposals/{proposal['id']}/accept", headers=ADMIN_HEADERS)
    planned = client.post(
        f"/ops/capability-proposals/{proposal['id']}/compile-plan",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Compile plan."},
    )

    assert accepted.status_code == 200
    assert planned.status_code == 200
    plan = planned.json()["implementation_plan"]
    compiled = plan["compiled_plan"]
    stage_ids = [stage["id"] for stage in compiled["stages"]]
    assert compiled["deploy_gate"]["required"] is True
    assert compiled["deploy_gate"]["model_facing_components_can_deploy"] is False
    assert stage_ids == [
        "registry_config",
        "task_template",
        "api_validation_rendering",
        "worker_handler",
        "ops_ui_surface_if_needed",
        "docs_tests",
        "post_deploy_smoke_plan",
    ]


def test_tool_cannot_close_capability_proposal(client):
    created = client.post("/capability-proposals/draft", headers=TOOL_HEADERS, json=capability_proposal_payload()).json()

    response = client.post(
        f"/capability-proposals/{created['id']}/close",
        headers=TOOL_HEADERS,
        json={"status": "accepted", "reason": "Looks useful."},
    )

    assert response.status_code == 403


def test_admin_can_accept_reject_or_close_capability_proposal(client):
    first = client.post("/capability-proposals/draft", headers=TOOL_HEADERS, json=capability_proposal_payload()).json()
    accepted = client.post(f"/capability-proposals/{first['id']}/accept", headers=ADMIN_HEADERS)
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    second = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(suggested_capability_id="printer_supply_status_alt.v1"),
    ).json()
    rejected = client.post(
        f"/capability-proposals/{second['id']}/reject",
        headers=ADMIN_HEADERS,
        json={"status": "rejected", "reason": "Not needed."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert "Not needed" in rejected.json()["review_notes"]


def test_capability_proposal_rejects_existing_capability_secrets_and_unsafe_requests(client):
    existing = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(suggested_capability_id="server_health.v1", suggested_task_type="server_health"),
    )
    secret = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(
            suggested_capability_id="secret_test.v1",
            original_request_preview="use token: xoxb-this-should-not-be-stored",
        ),
    )
    unsafe = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(
            suggested_capability_id="file_cleanup.v1",
            suggested_task_type="file_cleanup",
            purpose="Automatically reorganize all files on my server.",
        ),
    )

    assert existing.status_code == 422
    assert "already registered" in existing.text
    assert secret.status_code == 422
    assert "secret-like" in secret.text
    assert unsafe.status_code == 422
    assert "forbidden unsafe term" in unsafe.text
