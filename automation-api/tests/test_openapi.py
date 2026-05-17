from __future__ import annotations


def test_openapi_schema_is_generated(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert data["info"]["title"] == "Yggy Automation API"
    assert "/tasks/draft" in data["paths"]
    assert "/task-templates" in data["paths"]
    assert "/task-templates/{template_id}/draft" in data["paths"]
    assert "/tasks/{task_id}/propose-change" in data["paths"]
    assert "/task-change-proposals/{proposal_id}/approve" in data["paths"]
    assert "/maintenance/retention" in data["paths"]
