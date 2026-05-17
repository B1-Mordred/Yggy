from __future__ import annotations

import httpx

from conftest import ADMIN_HEADERS, TOOL_HEADERS


PUBLIC_IP = "93.184.216.34"


def public_resolver(host: str, resolver=None):
    return [PUBLIC_IP]


def test_sources_endpoint_lists_approved_public_sources(client):
    response = client.get("/sources", headers=TOOL_HEADERS)

    assert response.status_code == 200
    sources = response.json()
    assert {source["id"] for source in sources} >= {"open_webui_releases", "docker_blog"}
    assert all("token" not in source for source in sources)


def test_research_query_fetches_approved_rss_and_caches_items(client, monkeypatch):
    from app.services import research_service

    feed = """
<rss><channel>
  <item>
    <title>Open WebUI security release</title>
    <link>https://example.com/open-webui-release</link>
    <description>Fixes a local AI security issue. token = should-not-leak</description>
    <pubDate>Sun, 17 May 2026 08:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, text=feed, request=httpx.Request("GET", url))

    monkeypatch.setattr(research_service, "resolve_host_addresses", public_resolver)
    monkeypatch.setattr(research_service.httpx, "get", fake_get)

    response = client.post(
        "/research/query",
        headers=TOOL_HEADERS,
        json={"source_ids": ["open_webui_releases"], "query": "Open WebUI security", "limit": 5, "refresh": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["read_only"] is True
    assert body["source_content_is_untrusted"] is True
    assert body["source_ids"] == ["open_webui_releases"]
    assert body["item_count"] == 1
    assert body["items"][0]["title"] == "Open WebUI security release"
    assert body["items"][0]["summary"] == "Fixes a local AI security issue. [REDACTED]"
    assert calls[0][0] == "https://github.com/open-webui/open-webui/releases.atom"

    cached = client.get("/research/items?source_id=open_webui_releases", headers=TOOL_HEADERS)
    assert cached.status_code == 200
    assert cached.json()[0]["id"] == body["items"][0]["id"]


def test_research_rejects_unknown_source_id(client):
    response = client.post(
        "/research/query",
        headers=TOOL_HEADERS,
        json={"source_ids": ["not_registered"], "limit": 5},
    )

    assert response.status_code == 422
    assert "unknown or disabled approved source_id" in response.text


def test_research_blocks_private_and_local_source_addresses(client, tmp_path, monkeypatch):
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        """
version: 1
sources:
  - id: local_feed
    name: Local feed
    type: rss
    url: http://127.0.0.1/feed.xml
    categories:
      - local_ai
    trust_level: approved_test_feed
    enabled: true
    max_items: 5
""",
        encoding="utf-8",
    )
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        f"""
version: 1
allowed_discord_targets:
  - briefings
approval_thresholds:
  auto_allow:
    - L0_READ_ONLY
  initial_approval_required:
    - L1_NOTIFY_ONLY
  admin_required:
    - L2_LOCAL_WRITE
    - L3_EXTERNAL_SIDE_EFFECT
  manual_only:
    - L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE
source_policy:
  approved_sources_file: {sources}
  require_approved_sources_for_task_types:
    - topic_digest
  require_source_ids: true
  allow_web_query_sources: false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOMATION_POLICY_FILE", str(policy))

    response = client.post(
        "/research/query",
        headers=TOOL_HEADERS,
        json={"source_ids": ["local_feed"], "limit": 5, "refresh": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["item_count"] == 0
    assert body["errors"][0]["source_id"] == "local_feed"
    assert "private or non-public" in body["errors"][0]["detail"]


def test_research_role_cannot_approve_with_tool_key(client):
    response = client.post(
        "/approvals/not-real/approve",
        headers=TOOL_HEADERS,
        json={"nonce": "not-real"},
    )

    assert response.status_code == 403

    admin_items = client.get("/research/items", headers=ADMIN_HEADERS)
    assert admin_items.status_code == 200
