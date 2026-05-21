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


def create_planned_proposal(client) -> dict:
    proposal = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(),
    ).json()
    accepted = client.post(f"/capability-proposals/{proposal['id']}/accept", headers=ADMIN_HEADERS)
    assert accepted.status_code == 200
    planned = client.post(
        f"/ops/capability-proposals/{proposal['id']}/plan",
        headers={**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-proposal"},
        json={"reason": "Plan implementation."},
    )
    assert planned.status_code == 200
    return planned.json()


def test_tool_key_cannot_create_or_list_capability_implementation_runs(client):
    planned = create_planned_proposal(client)

    created = client.post(
        "/capability-implementation-runs",
        headers=TOOL_HEADERS,
        json={"proposal_id": planned["id"], "created_by": "bragi"},
    )
    listed = client.get("/capability-implementation-runs", headers=TOOL_HEADERS)

    assert created.status_code == 403
    assert listed.status_code == 403


def test_admin_can_queue_and_complete_capability_implementation_run(client):
    planned = create_planned_proposal(client)

    created = client.post(
        "/capability-implementation-runs",
        headers=ADMIN_HEADERS,
        json={"proposal_id": planned["id"], "created_by": "local_cli", "reason": "Implement locally."},
    )
    duplicate = client.post(
        "/capability-implementation-runs",
        headers=ADMIN_HEADERS,
        json={"proposal_id": planned["id"], "created_by": "local_cli"},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "queued"
    assert body["proposal_id"] == planned["id"]
    assert body["plan_id"] == planned["implementation_plan"]["id"]
    assert body["capability_id"] == "printer_supply_snmp.v1"
    assert body["branch"].startswith("capability/printer_supply_snmp-")
    assert body["execution"] == {
        "creates_task": False,
        "creates_approval": False,
        "can_run_automation": False,
        "can_push": False,
        "local_commit_only": True,
    }
    assert body["operator_handoff"]["cli_command"] == f"python scripts/implement_capability_plan.py --proposal-id {planned['id']}"
    assert duplicate.status_code == 409
    assert "active implementation run" in duplicate.text

    running = client.patch(
        f"/capability-implementation-runs/{body['id']}",
        headers=ADMIN_HEADERS,
        json={"status": "running", "branch": body["branch"]},
    )
    missing_commit = client.patch(
        f"/capability-implementation-runs/{body['id']}",
        headers=ADMIN_HEADERS,
        json={"status": "completed"},
    )
    completed = client.patch(
        f"/capability-implementation-runs/{body['id']}",
        headers=ADMIN_HEADERS,
        json={
            "status": "completed",
            "commit_sha": "abcdef1234567890",
            "summary": "Implemented and validated.",
            "test_results": {"commands": [{"command": "pytest", "returncode": 0}]},
        },
    )
    regress = client.patch(
        f"/capability-implementation-runs/{body['id']}",
        headers=ADMIN_HEADERS,
        json={"status": "running"},
    )

    assert running.status_code == 200
    assert running.json()["status"] == "running"
    assert missing_commit.status_code == 409
    assert "commit_sha" in missing_commit.text
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None
    assert completed.json()["commit_sha"] == "abcdef1234567890"
    assert regress.status_code == 409


def test_cannot_queue_capability_implementation_without_active_plan(client):
    proposal = client.post(
        "/capability-proposals/draft",
        headers=TOOL_HEADERS,
        json=capability_proposal_payload(),
    ).json()

    pending = client.post(
        "/capability-implementation-runs",
        headers=ADMIN_HEADERS,
        json={"proposal_id": proposal["id"], "created_by": "local_cli"},
    )
    accepted = client.post(f"/capability-proposals/{proposal['id']}/accept", headers=ADMIN_HEADERS).json()
    accepted_without_plan = client.post(
        "/capability-implementation-runs",
        headers=ADMIN_HEADERS,
        json={"proposal_id": accepted["id"], "created_by": "local_cli"},
    )

    assert pending.status_code == 409
    assert "implementation_planned" in pending.text
    assert accepted_without_plan.status_code == 409
    assert "implementation_planned" in accepted_without_plan.text


def test_capability_implementation_run_endpoints_are_not_in_openapi(client):
    schema = client.get("/openapi.json").json()

    assert "/capability-implementation-runs" not in schema["paths"]
    assert "/capability-implementation-runs/{run_id}" not in schema["paths"]
