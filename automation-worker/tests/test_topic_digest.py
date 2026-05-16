from __future__ import annotations

import pytest

from worker.handlers.topic_digest import run_topic_digest


class FakeSummarizer:
    def __init__(self, message: str | None = None, error: Exception | None = None) -> None:
        self.message = message
        self.error = error

    def summarize_digest(self, task_config: dict, items: list[dict], errors: list[dict]) -> str | None:
        if self.error:
            raise self.error
        return self.message


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
            "sources": [{"type": "rss", "url": "https://example.com/feed.xml"}],
            "filters": {"include": ["Open WebUI", "Docker"], "exclude": ["sponsored"]},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "timeout_seconds": 120},
        },
        rss_fetcher=fetcher,
    )
    assert result["status"] == "dry_run"
    assert result["source_count"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Open WebUI security release"
    assert "Review the dry-run output" in result["message"]


def test_topic_digest_web_query_item_is_data_only():
    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"type": "web_query", "query": "Open WebUI Ollama security"}],
            "filters": {"include": ["Open WebUI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True},
        }
    )
    assert result["items"][0]["type"] == "web_query"
    assert result["items"][0]["title"] == "Web query configured"


def test_topic_digest_uses_enabled_summarizer():
    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"type": "web_query", "query": "Open WebUI Ollama security"}],
            "filters": {"include": ["Open WebUI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True},
        },
        summarizer=FakeSummarizer("**Digest**\n\nLLM summary"),
    )

    assert result["summary_mode"] == "llm"
    assert result["message"] == "**Digest**\n\nLLM summary"


def test_topic_digest_falls_back_when_summarizer_errors():
    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"type": "web_query", "query": "Open WebUI Ollama security"}],
            "filters": {"include": ["Open WebUI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True},
        },
        summarizer=FakeSummarizer(error=TimeoutError("slow model")),
    )

    assert result["summary_mode"] == "deterministic"
    assert result["summary_error"] == "TimeoutError"
    assert "Review the dry-run output" in result["message"]
