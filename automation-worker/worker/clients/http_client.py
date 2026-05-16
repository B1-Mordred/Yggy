from __future__ import annotations

import httpx


def fetch_text(url: str, timeout: int = 20) -> str:
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text
