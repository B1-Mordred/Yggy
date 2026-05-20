from __future__ import annotations

import pytest

from worker.handlers.topic_digest import run_topic_digest
from worker.source_registry import ApprovedSource, SourceRegistry, source_config_type


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


def test_preapproved_source_type_detection_avoids_feed_substrings():
    assert source_config_type("Fact-checking", "https://sciencefeedback.co/") == "http"
    assert source_config_type("News feed directory", "https://www.deutschlandfunk.de/rss-angebot-102.html") == "http"
    assert source_config_type("RSS feed", "https://www.nasa.gov/feed/") == "rss"
    assert source_config_type("News feed", "https://rss.dw.com/rdf/rss-en-all") == "rss"


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


def http_source(**overrides) -> ApprovedSource:
    values = {
        "id": "example_http",
        "name": "Example HTTP",
        "type": "http",
        "url": "https://example.com/",
        "categories": ["preapproved"],
        "trust_level": "ai_safe_b_terms_check",
        "enabled": True,
        "max_items": 1,
        "description": "Example approved HTTP source.",
        "ingestion_mode": "metadata_only",
        "ai_safe_fit": "B - terms-check/variable",
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


def test_topic_digest_deduplicates_items_by_link():
    feed = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>Same AI security item</title>
        <link>https://example.com/same</link>
        <description>First source copy.</description>
      </item>
      <item>
        <title>Same AI security item rewritten</title>
        <link>https://example.com/same/</link>
        <description>Duplicate source copy.</description>
      </item>
      <item>
        <title>Different AI software item</title>
        <link>https://example.com/different</link>
        <description>Unique source copy.</description>
      </item>
    </channel></rss>
    """

    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_feed", "type": "rss", "url": "https://example.com/feed.xml"}],
            "filters": {"include": ["AI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "timeout_seconds": 120},
        },
        rss_fetcher=lambda url, timeout: feed,
        source_registry=registry(rss_source()),
    )

    assert [item["link"] for item in result["items"]] == ["https://example.com/same", "https://example.com/different"]
    assert result["deduplicated_count"] == 1


def test_topic_digest_prefers_atom_alternate_links():
    feed = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>AI security item</title>
        <link rel="replies" href="https://example.com/comments"/>
        <link rel="alternate" href="https://example.com/article"/>
        <summary>AI security summary.</summary>
      </entry>
    </feed>
    """

    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_feed", "type": "rss", "url": "https://example.com/feed.xml"}],
            "filters": {"include": ["AI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "timeout_seconds": 120},
        },
        rss_fetcher=lambda url, timeout: feed,
        source_registry=registry(rss_source()),
    )

    assert result["items"][0]["link"] == "https://example.com/article"


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


def test_topic_digest_metadata_only_http_source_does_not_fetch():
    called = False

    def http_fetcher(url: str, timeout: int) -> str:
        nonlocal called
        called = True
        raise AssertionError("metadata-only HTTP source should not be fetched")

    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_http", "type": "http", "url": "https://example.com/"}],
            "filters": {"include": ["Example"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True},
        },
        http_fetcher=http_fetcher,
        source_registry=registry(http_source()),
    )

    assert called is False
    assert result["items"][0]["title"] == "Example HTTP"
    assert result["items"][0]["source_ingestion_mode"] == "metadata_only"
    assert result["source_health"][0]["ingestion_mode"] == "metadata_only"


def test_topic_digest_http_summary_source_fetches_bounded_page():
    calls = []

    def http_fetcher(url: str, timeout: int) -> str:
        calls.append((url, timeout))
        return "<html><head><title>CISA advisory</title></head><body>Known exploited vulnerability update.</body></html>"

    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_http", "type": "http", "url": "https://example.com/"}],
            "filters": {"include": ["CISA"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "timeout_seconds": 30},
        },
        http_fetcher=http_fetcher,
        source_registry=registry(http_source(ingestion_mode="http_summary", trust_level="ai_safe_a_open")),
    )

    assert calls == [("https://example.com/", 30)]
    assert result["items"][0]["title"] == "CISA advisory"
    assert result["items"][0]["source_ingestion_mode"] == "http_summary"


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


def test_sectioned_topic_digest_can_disable_llm_summary():
    google_feed = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>AI prompt injection security advisory</title>
        <link>https://example.com/ai-security</link>
        <description>AI agent prompt injection threat with mitigation guidance.</description>
      </item>
    </channel></rss>
    """
    nvidia_feed = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>NVIDIA GPU accelerator platform update</title>
        <link>https://example.com/gpu</link>
        <description>AI hardware accelerator news for inference systems.</description>
      </item>
    </channel></rss>
    """

    def fetcher(url: str, timeout: int) -> str:
        if "security" in url:
            return google_feed
        return nvidia_feed

    result = run_topic_digest(
        {
            "name": "Sectioned Digest",
            "sources": [
                {"source_id": "google_security_blog", "type": "rss", "url": "https://example.com/security.xml"},
                {"source_id": "nvidia_developer_blog", "type": "rss", "url": "https://example.com/nvidia.xml"},
            ],
            "filters": {"include": [], "exclude": []},
            "output": {
                "format": (
                    "AI security news; AI hardware news; AI software news; "
                    "security issues related to this system; EU political news; German political news"
                )
            },
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "llm_summary_enabled": False},
        },
        rss_fetcher=fetcher,
        summarizer=FakeSummarizer(error=AssertionError("summarizer should be skipped")),
        source_registry=registry(
            rss_source(
                id="google_security_blog",
                name="Google Security Blog",
                url="https://example.com/security.xml",
                categories=["ai_security"],
            ),
            rss_source(
                id="nvidia_developer_blog",
                name="NVIDIA Developer Blog",
                url="https://example.com/nvidia.xml",
                categories=["ai_hardware"],
            ),
        ),
    )

    assert result["summary_mode"] == "deterministic"
    assert "AI Security News" in result["message"]
    assert "AI Hardware News" in result["message"]
    assert "AI prompt injection security advisory" in result["message"]
    assert "NVIDIA GPU accelerator platform update" in result["message"]


def test_topic_digest_quality_is_ok_when_thresholds_are_met():
    feed = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>AI security item</title>
        <link>https://example.com/ai-security</link>
        <description>AI vulnerability advisory.</description>
      </item>
      <item>
        <title>AI software item</title>
        <link>https://example.com/ai-software</link>
        <description>AI model release notes.</description>
      </item>
    </channel></rss>
    """

    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"source_id": "example_feed", "type": "rss", "url": "https://example.com/feed.xml"}],
            "filters": {"include": ["AI"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "timeout_seconds": 120},
            "quality": {
                "min_items": 2,
                "min_successful_sources": 1,
                "alert_on_source_errors": True,
                "alert_target": "alerts",
            },
        },
        rss_fetcher=lambda url, timeout: feed,
        source_registry=registry(rss_source()),
    )

    assert result["quality"]["status"] == "ok"
    assert result["quality"]["alert_needed"] is False
    assert result["quality"]["metrics"]["successful_source_count"] == 1
    assert result["quality"]["metrics"]["item_count"] == 2


def test_topic_digest_quality_marks_source_errors_degraded():
    good_feed = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>Open WebUI security release</title>
        <link>https://example.com/open-webui</link>
        <description>Docker deployment hardening update.</description>
      </item>
    </channel></rss>
    """

    def fetcher(url: str, timeout: int) -> str:
        if "broken" in url:
            raise TimeoutError("feed timed out")
        return good_feed

    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [
                {"source_id": "broken_feed", "type": "rss", "url": "https://example.com/broken.xml"},
                {"source_id": "example_feed", "type": "rss", "url": "https://example.com/feed.xml"},
            ],
            "filters": {"include": ["Open WebUI", "Docker"], "exclude": []},
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True, "timeout_seconds": 120},
            "quality": {
                "min_items": 2,
                "min_successful_sources": 2,
                "alert_on_source_errors": True,
                "alert_target": "alerts",
            },
        },
        rss_fetcher=fetcher,
        source_registry=registry(
            rss_source(id="broken_feed", name="Broken feed", url="https://example.com/broken.xml"),
            rss_source(),
        ),
    )

    assert result["quality"]["status"] == "degraded"
    assert result["quality"]["alert_needed"] is True
    assert {reason["code"] for reason in result["quality"]["reasons"]} == {
        "too_few_items",
        "too_few_successful_sources",
        "source_errors",
    }
    assert result["quality"]["metrics"]["successful_source_count"] == 1
    assert result["quality"]["metrics"]["failed_source_count"] == 1


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
