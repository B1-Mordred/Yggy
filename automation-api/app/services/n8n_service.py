from __future__ import annotations

import httpx


class N8nService:
    def call_webhook(self, url: str, payload: dict, token: str | None = None) -> dict:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = httpx.post(url, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        return {"status_code": response.status_code}
