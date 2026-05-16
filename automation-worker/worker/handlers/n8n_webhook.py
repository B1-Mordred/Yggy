from __future__ import annotations

import httpx


def call_n8n_webhook(url: str, payload: dict, token: str | None = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.post(url, json=payload, headers=headers, timeout=20)
    response.raise_for_status()
    return {"status_code": response.status_code}
