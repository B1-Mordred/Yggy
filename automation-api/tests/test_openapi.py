from __future__ import annotations


def test_openapi_schema_is_generated(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert data["info"]["title"] == "Yggy Automation API"
    assert "/capabilities" in data["paths"]
    assert "/capabilities/validate-intent" in data["paths"]
    assert "/capabilities/prepare-yggdrasil-request" in data["paths"]
    assert "/capability-proposals/draft" in data["paths"]
    assert "/capability-proposals/{proposal_id}/close" in data["paths"]
    assert "/capability-implementation-runs" not in data["paths"]
    assert "/capability-implementation-runs/{run_id}" not in data["paths"]
    assert "/channels/events" in data["paths"]
    assert "/channels/events/{event_id}" in data["paths"]
    assert "/sources" in data["paths"]
    assert "/research/query" in data["paths"]
    assert "/research/topic-digest-suggestion" in data["paths"]
    assert "/research/items" in data["paths"]
    assert "/tasks/draft" in data["paths"]
    assert "/task-templates" in data["paths"]
    assert "/task-templates/{template_id}/draft" in data["paths"]
    assert "/tasks/{task_id}/propose-change" in data["paths"]
    assert "/task-change-proposals/{proposal_id}/approve" in data["paths"]
    assert "/maintenance/retention" in data["paths"]
