from __future__ import annotations

import httpx


FETCH_HEADERS = {"User-Agent": "YggyAutomation/1.0 (+local personal automation)"}


def fetch_rss(url: str, timeout: int = 20) -> str:
    response = httpx.get(url, timeout=timeout, follow_redirects=True, headers=FETCH_HEADERS)
    response.raise_for_status()
    return response.text
