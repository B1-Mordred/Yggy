from __future__ import annotations

from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import ApprovalModel, TaskConfigVersionModel, TaskModel, utcnow
from conftest import ADMIN_HEADERS, TOOL_HEADERS, sample_task


def test_draft_task_creates_approval_linked_config_version(client):
    response = client.post("/tasks/draft", headers=TOOL_HEADERS, json=sample_task("versioned_draft"))

    assert response.status_code == 201
    body = response.json()
    approval = body["approval"]
    with Session(get_engine()) as session:
        versions = (
            session.query(TaskConfigVersionModel)
            .filter(TaskConfigVersionModel.task_id == "versioned_draft")
            .order_by(TaskConfigVersionModel.version)
            .all()
        )
        assert len(versions) == 1
        assert versions[0].version == 1
        assert versions[0].change_type == "draft"
        assert versions[0].actor_role == "tool"
        assert versions[0].approval_id == approval["id"]
        assert versions[0].config["id"] == "versioned_draft"

    ops_response = client.get("/ops/status", headers=ADMIN_HEADERS)

    assert ops_response.status_code == 200
    status = ops_response.json()
    assert status["counts"]["pending_approvals"] == 1
    assert status["counts"]["pending_proposals"] == 1
    assert status["counts"]["pending_general_approvals"] == 0
    assert status["pending_general_approvals"] == []
    pending = status["pending_proposals"][0]
    assert pending["id"] == approval["id"]
    assert pending["review"]["config_diff"]["version"] == 1
    assert pending["review"]["config_diff"]["change_type"] == "draft"
    assert pending["review"]["config_diff"]["diff"]["counts"]["added"] > 0


def test_update_then_request_approval_links_latest_version_with_diff(client):
    create_response = client.post(
        "/tasks/draft",
        headers=TOOL_HEADERS,
        json=sample_task("versioned_update", "L0_READ_ONLY"),
    )
    assert create_response.status_code == 201

    updated = sample_task(
        "versioned_update",
        "L0_READ_ONLY",
        trigger={"cron": "15 8 * * 1-5"},
        filters={"include": ["Open WebUI", "Ollama"]},
    )
    update_response = client.put("/tasks/versioned_update", headers=TOOL_HEADERS, json=updated)
    assert update_response.status_code == 200

    approval_response = client.post("/tasks/versioned_update/request-approval", headers=TOOL_HEADERS)
    assert approval_response.status_code == 201
    approval_id = approval_response.json()["id"]

    with Session(get_engine()) as session:
        versions = (
            session.query(TaskConfigVersionModel)
            .filter(TaskConfigVersionModel.task_id == "versioned_update")
            .order_by(TaskConfigVersionModel.version)
            .all()
        )
        assert [version.version for version in versions] == [1, 2]
        assert versions[1].change_type == "update"
        assert versions[1].approval_id == approval_id

    ops_response = client.get("/ops/status", headers=ADMIN_HEADERS)

    assert ops_response.status_code == 200
    status = ops_response.json()
    approval = next(item for item in status["pending_proposals"] if item["id"] == approval_id)
    assert approval["review"]["config_diff"]["change_type"] == "update"
    changed_paths = {item["path"] for item in approval["review"]["config_diff"]["diff"]["changed"]}
    assert "trigger.cron" in changed_paths
    assert "filters.include[1]" in {item["path"] for item in approval["review"]["config_diff"]["diff"]["added"]}


def test_ops_reviews_filters_proposals_and_general_approvals(client):
    proposal_response = client.post(
        "/tasks/draft",
        headers=TOOL_HEADERS,
        json=sample_task("review_filter_proposal"),
    )
    assert proposal_response.status_code == 201
    proposal_approval_id = proposal_response.json()["approval"]["id"]
    general_config = sample_task("review_filter_general")
    with Session(get_engine()) as session:
        session.add(
            TaskModel(
                id=general_config["id"],
                name=general_config["name"],
                type=general_config["type"],
                enabled=False,
                owner=general_config["owner"],
                created_by=general_config["created_by"],
                approval_level=general_config["policy"]["approval_level"],
                status="pending_approval",
                config=general_config,
            )
        )
        session.flush()
        session.add(
            ApprovalModel(
                id="approval-review-general",
                task_id=general_config["id"],
                approval_level=general_config["policy"]["approval_level"],
                requested_by="manual_operator",
                status="pending",
                summary="General manual approval",
                risk="medium",
                nonce_hash="nonce-hash",
                created_at=utcnow(),
            )
        )
        session.commit()

    proposals = client.get("/ops/reviews?kind=proposals", headers=ADMIN_HEADERS)
    approvals = client.get("/ops/reviews?kind=approvals", headers=ADMIN_HEADERS)
    draft_proposals = client.get("/ops/reviews?kind=proposals&change_type=draft", headers=ADMIN_HEADERS)
    searched_approvals = client.get("/ops/reviews?kind=approvals&q=manual", headers=ADMIN_HEADERS)
    task_filtered = client.get("/ops/reviews?kind=all&task_id=review_filter_general", headers=ADMIN_HEADERS)

    assert proposals.status_code == 200
    assert [item["id"] for item in proposals.json()["reviews"]] == [proposal_approval_id]
    assert proposals.json()["counts"]["matched"] == 1
    assert approvals.status_code == 200
    assert [item["id"] for item in approvals.json()["reviews"]] == ["approval-review-general"]
    assert draft_proposals.status_code == 200
    assert draft_proposals.json()["reviews"][0]["review"]["config_diff"]["change_type"] == "draft"
    assert searched_approvals.status_code == 200
    assert [item["id"] for item in searched_approvals.json()["reviews"]] == ["approval-review-general"]
    assert task_filtered.status_code == 200
    assert [item["id"] for item in task_filtered.json()["reviews"]] == ["approval-review-general"]
