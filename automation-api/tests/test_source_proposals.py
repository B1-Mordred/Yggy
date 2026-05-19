from __future__ import annotations

from conftest import ADMIN_HEADERS, TOOL_HEADERS


def source_payload(**overrides):
    source = {
        "id": "example_public_feed",
        "name": "Example Public Feed",
        "type": "rss",
        "url": "https://example.com/feed.xml",
        "categories": ["preapproved", "security_news"],
        "trust_level": "operator_review",
        "enabled": True,
        "max_items": 5,
        "description": "Example source proposed by Bragi.",
        "region": "Global",
        "languages": ["en"],
        "source_type_label": "News feed",
        "update_cadence": "Regular",
        "ingestion_notes": "Use feed metadata/snippets unless terms allow more.",
        "ai_safe_fit": "B - terms-check/variable",
        "ingestion_mode": "feed_metadata",
    }
    source.update(overrides)
    return {"source": source, "summary": "Please review this public source.", "requested_by": "bragi"}


def test_tool_can_propose_source_but_cannot_approve(client):
    response = client.post("/sources/propose", headers=TOOL_HEADERS, json=source_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["source_id"] == "example_public_feed"
    assert "nonce" not in body

    approve = client.post(
        f"/source-proposals/{body['id']}/approve",
        headers=TOOL_HEADERS,
        json={"nonce": "tool-must-not-have-nonce"},
    )
    assert approve.status_code == 403


def test_admin_can_approve_and_apply_source_proposal_with_valid_nonce(client):
    created = client.post("/sources/propose", headers=ADMIN_HEADERS, json=source_payload()).json()
    assert created["nonce"]
    bad = client.post(
        f"/source-proposals/{created['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"nonce": "wrong"},
    )
    assert bad.status_code == 403

    approved = client.post(
        f"/source-proposals/{created['id']}/approve",
        headers=ADMIN_HEADERS,
        json={"nonce": created["nonce"]},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    applied = client.post(f"/source-proposals/{created['id']}/apply", headers=ADMIN_HEADERS)
    assert applied.status_code == 200
    body = applied.json()
    assert body["proposal"]["status"] == "applied"
    assert body["apply"]["source_entry"]["id"] == "example_public_feed"
    assert "configs/sources/approved_sources.yaml" in body["apply"]["registry_file"]


def test_source_proposal_rejects_duplicate_private_and_web_query_sources(client):
    duplicate = client.post(
        "/sources/propose",
        headers=TOOL_HEADERS,
        json=source_payload(id="open_webui_releases", url="https://example.com/other.xml"),
    )
    private = client.post(
        "/sources/propose",
        headers=TOOL_HEADERS,
        json=source_payload(id="private_feed", url="https://127.0.0.1/feed.xml"),
    )
    web_query = client.post(
        "/sources/propose",
        headers=TOOL_HEADERS,
        json=source_payload(id="broad_query", type="web_query", url=None, query="latest everything"),
    )

    assert duplicate.status_code == 422
    assert "already exists" in duplicate.text
    assert private.status_code == 422
    assert "private or local" in private.text
    assert web_query.status_code == 422
    assert "rss or http" in web_query.text
