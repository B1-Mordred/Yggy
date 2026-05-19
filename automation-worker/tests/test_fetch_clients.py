from __future__ import annotations

import httpx

from worker.clients.http_client import fetch_text
from worker.clients.rss_client import fetch_rss


class Response:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=httpx.Request("GET", "https://example.test"), response=httpx.Response(self.status_code))


def test_fetch_rss_retries_without_custom_user_agent_after_403(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return Response(403, "denied")
        return Response(200, "<rss></rss>")

    monkeypatch.setattr("worker.clients.rss_client.httpx.get", fake_get)

    assert fetch_rss("https://example.test/feed.xml") == "<rss></rss>"
    assert "headers" in calls[0]
    assert "headers" not in calls[1]


def test_fetch_text_retries_without_custom_user_agent_after_403(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return Response(403, "denied")
        return Response(200, "{}")

    monkeypatch.setattr("worker.clients.http_client.httpx.get", fake_get)

    assert fetch_text("https://example.test/data.json") == "{}"
    assert "headers" in calls[0]
    assert "headers" not in calls[1]
