from __future__ import annotations

from conftest import ADMIN_HEADERS, TOOL_HEADERS


def capability_proposal_payload(**overrides) -> dict:
    payload = {
        "title": "UPS Battery Monitoring",
        "requested_by": "bragi",
        "source_channel": "discord",
        "original_request_preview": "Track UPS battery health and alert me.",
        "purpose": "Monitor approved UPS battery status and notify on low charge or degraded health.",
        "suggested_capability_id": "ups_battery_status.v1",
        "suggested_task_type": "ups_battery_status",
        "likely_approval_level": "L1_NOTIFY_ONLY",
        "required_inputs": ["approved UPS endpoint ID", "polling schedule", "low battery threshold"],
        "safety_rules": ["must use approved read-only UPS endpoint IDs", "must not control UPS power state"],
        "non_goals": ["no arbitrary shell execution", "no network scanning"],
        "review_notes": "Useful but unsupported.",
    }
    payload.update(overrides)
    return payload


def test_capability_gap_seed_and_match_endpoint(client):
    listed = client.get("/capability-gaps", headers=TOOL_HEADERS)
    matched = client.post(
        "/capability-gaps/match",
        headers=TOOL_HEADERS,
        json={"text": "frist we need the new api endpoint for monitoring disk usage, dont you think?"},
    )

    assert listed.status_code == 200
    assert any(gap["id"] == "storage_usage.v1" for gap in listed.json()["gaps"])
    assert matched.status_code == 200
    assert matched.json()["matched"] is True
    assert matched.json()["gap"]["suggested_capability_id"] == "storage_usage.v1"


def test_capability_proposal_auto_generates_gap(client):
    proposal = client.post("/capability-proposals/draft", headers=TOOL_HEADERS, json=capability_proposal_payload())
    listed = client.get("/capability-gaps", headers=TOOL_HEADERS)
    match = client.post(
        "/capability-gaps/match",
        headers=TOOL_HEADERS,
        json={"text": "please monitor my UPS battery health and alert me"},
    )

    assert proposal.status_code == 201
    assert listed.status_code == 200
    gap = next(item for item in listed.json()["gaps"] if item["id"] == "ups_battery_status.v1")
    assert gap["source"] == "capability_proposal"
    assert gap["linked_capability_proposal_id"] == proposal.json()["id"]
    assert match.status_code == 200
    assert match.json()["matched"] is True
    assert match.json()["gap"]["suggested_capability_id"] == "ups_battery_status.v1"


def test_ops_can_update_capability_gap(client):
    headers = {**ADMIN_HEADERS, "X-Yggy-Ops-Action": "capability-gap"}
    payload = {
        "id": "gpu_temperature.v1",
        "enabled": True,
        "status": "active",
        "route": "propose_new_capability",
        "title": "GPU Temperature Monitoring",
        "purpose": "Route GPU temperature monitoring requests to capability backlog until a bounded endpoint exists.",
        "suggested_capability_id": "gpu_temperature.v1",
        "suggested_task_type": "gpu_temperature",
        "likely_approval_level": "L1_NOTIFY_ONLY",
        "trigger_terms": ["gpu temperature", "gpu temp"],
        "context_terms": ["monitor", "threshold", "alert"],
        "exclude_terms": [],
        "required_inputs": ["approved GPU metrics endpoint ID", "temperature thresholds"],
        "safety_rules": ["must use a read-only metrics endpoint", "must not expose shell access"],
        "non_goals": ["no GPU overclocking", "no service restarts"],
        "review_notes": "Configured from ops test.",
    }

    saved = client.put("/ops/capability-gaps/gpu_temperature.v1", headers=headers, json=payload)
    matched = client.post(
        "/capability-gaps/match",
        headers=TOOL_HEADERS,
        json={"text": "monitor GPU temperature and alert if it crosses a threshold"},
    )

    assert saved.status_code == 200
    assert saved.json()["source"] == "ops_dashboard"
    assert matched.status_code == 200
    assert matched.json()["matched"] is True
    assert matched.json()["gap"]["suggested_capability_id"] == "gpu_temperature.v1"
