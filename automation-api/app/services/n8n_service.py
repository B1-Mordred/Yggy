from __future__ import annotations

from urllib.parse import urljoin

import httpx


class N8nService:
    def __init__(self, base_url: str = "http://n8n:5678") -> None:
        self.base_url = base_url.rstrip("/") + "/"

    def call_approved_webhook(self, path: str, payload: dict, shared_secret: str) -> dict:
        if not path.startswith(("/webhook/", "/webhook-test/")):
            raise ValueError("n8n webhook path must be an internal webhook path")
        if not shared_secret:
            raise ValueError("shared_secret is required")
        response = httpx.post(
            urljoin(self.base_url, path.lstrip("/")),
            json=payload,
            headers={"X-Yggy-Webhook-Token": shared_secret},
            timeout=20,
        )
        response.raise_for_status()
        return {"status_code": response.status_code}
