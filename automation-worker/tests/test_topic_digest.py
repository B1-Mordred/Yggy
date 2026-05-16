from __future__ import annotations

import pytest

from worker.handlers.topic_digest import run_topic_digest


def test_topic_digest_requires_sources_when_required():
    with pytest.raises(ValueError):
        run_topic_digest({"name": "Digest", "sources": [], "policy": {"require_sources": True}})


def test_topic_digest_returns_bounded_items():
    result = run_topic_digest(
        {
            "name": "Digest",
            "sources": [{"type": "rss", "url": "https://example.com/feed.xml"}],
            "policy": {"require_sources": True, "max_items": 10},
            "runtime": {"dry_run": True},
        }
    )
    assert result["status"] == "dry_run"
    assert result["source_count"] == 1
