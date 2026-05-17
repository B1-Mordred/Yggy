from __future__ import annotations

import pytest

from worker.handlers.topic_digest import run_topic_digest
from worker.source_registry import ApprovedSource, SourceRegistry


class FakeSummarizer:
    def __init__(self, message: str | None = None, error: Exception | None = None) -> None:
        self.message = message
        self.error = error

    def summarize_digest(self, task_config: dict, items: list[dict], errors: list[dict]) -> str | None:
        if self.error:
            raise self.error
        return self.message


def registry(*sources: ApprovedSource) -> SourceRegistry:
    return SourceRegistry(list(sources))


def rss_source(**overrides) -> ApprovedSource:
    values = {
        "id": "example_feed",
        "name": "Example feed",
        "type": "rss",
        "url": "https://example.com/feed.xml",
        "categories": ["local_ai"],
        "trust_level": "approved_test_feed",
        "enabled": True,
        "max_items": 5,
    }
    values.update(overrides)
    return ApprovedSource(**values)


def web_query_source(**overrides) -> ApprovedSource:
    values = {
        "id": "example_query",
        "name": "Example query",
        "type": "web_query",
        "query": "Open WebUI Ollama security",
        "categories": ["local_ai"],
        "trust_level": "approved_test_query",
        "enabled": True,
        "max_items": 1,
    }
    values.update(overrides)
    return ApprovedSource(**values)


def test_topic_digest_requires_sources_when_required():
    with pytest.raises(ValueError):
        run_topic_digest({"name": "Digest", "sources": [], "policy": {"require_sources": True}})


def test_topic_digest_returns_bounded_items():
    feed = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>Open WebUI security release</title>
        <link>https://example.com/open-webui</link>
        <description>Docker deployment hardening update.</description>
        <pubDate>Sat, 16 May 2026 08:00:00 +0000</pubDate>
      </item>
      <item>
        <title>Sponsored unrelated post</title>
        <link>https://example.com/ad</link>
        <description>sponsored</description>
      </item>
    </channel></rss>
    """

    def fetcher(url: str, timeout: int) -> str:
        assert url == "https://example.com/feed.xml"
        assert timeout == 120
        return feed

    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_feed", "type": "rss", "url": "https://example.com/feed.xml"}],
            "filters": {"include": ["Open WebUI", "Docker"], "exclude": ["sponsored"]},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "timeout_seconds": 120},
        },
        rss_fetcher=fetcher,
        source_registry=registry(rss_source()),
    )
    assert result["status"] == "dry_run"
    assert result["source_count"] == 1
    assert result["approved_source_count"] == 1
    assert result["source_health"][0]["status"] == "ok"
    assert result["source_health"][0]["trust_level"] == "approved_test_feed"
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Open WebUI security release"
    assert result["items"][0]["source_id"] == "example_feed"
    assert result["items"][0]["source_trust_level"] == "approved_test_feed"
    assert "Review the dry-run output" in result["message"]


def test_topic_digest_web_query_item_is_data_only():
    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_query", "type": "web_query", "query": "Open WebUI Ollama security"}],
            "filters": {"include": ["Open WebUI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True},
        },
        source_registry=registry(web_query_source()),
    )
    assert result["items"][0]["type"] == "web_query"
    assert result["items"][0]["title"] == "Web query configured"
    assert result["items"][0]["source_id"] == "example_query"


def test_topic_digest_uses_enabled_summarizer():
    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_query", "type": "web_query", "query": "Open WebUI Ollama security"}],
            "filters": {"include": ["Open WebUI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True},
        },
        summarizer=FakeSummarizer("**Digest**\n\nLLM summary"),
        source_registry=registry(web_query_source()),
    )

    assert result["summary_mode"] == "llm"
    assert result["message"] == "**Digest**\n\nLLM summary"


def test_topic_digest_falls_back_when_summarizer_errors():
    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_query", "type": "web_query", "query": "Open WebUI Ollama security"}],
            "filters": {"include": ["Open WebUI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True},
        },
        summarizer=FakeSummarizer(error=TimeoutError("slow model")),
        source_registry=registry(web_query_source()),
    )

    assert result["summary_mode"] == "deterministic"
    assert result["summary_error"] == "TimeoutError"
    assert "Review the dry-run output" in result["message"]


def test_topic_digest_blocks_unapproved_fetch_targets():
    called = False

    def fetcher(url: str, timeout: int) -> str:
        nonlocal called
        called = True
        return "<rss><channel></channel></rss>"

    with pytest.raises(ValueError, match="no approved enabled sources"):
        run_topic_digest(
            {
                "name": "Digest",
                "sources": [{"type": "rss", "url": "https://example.com/feed.xml"}],
                "filters": {"include": [], "exclude": []},
                "policy": {"require_sources": True, "max_items": 10},
                "runtime": {"dry_run": True, "timeout_seconds": 120},
            },
            rss_fetcher=fetcher,
            source_registry=registry(rss_source()),
        )

    assert called is False


def test_topic_digest_records_disabled_source_as_blocked():
    feed = """<?xml version="1.0"?>
    <rss><channel>
      <item><title>Open WebUI security release</title><link>https://example.com/open-webui</link></item>
    </channel></rss>
    """
    fetched_urls: list[str] = []

    def fetcher(url: str, timeout: int) -> str:
        fetched_urls.append(url)
        return feed

    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [
                {"source_id": "disabled_feed", "type": "rss", "url": "https://example.com/disabled.xml"},
                {"source_id": "example_feed", "type": "rss", "url": "https://example.com/feed.xml"},
            ],
            "filters": {"include": [], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "timeout_seconds": 120},
        },
        rss_fetcher=fetcher,
        source_registry=registry(
            rss_source(id="disabled_feed", name="Disabled feed", url="https://example.com/disabled.xml", enabled=False),
            rss_source(),
        ),
    )

    assert fetched_urls == ["https://example.com/feed.xml"]
    assert result["source_health"][0]["status"] == "blocked"
    assert result["source_health"][0]["error"] == "source_disabled"
    assert result["source_health"][1]["status"] == "ok"
