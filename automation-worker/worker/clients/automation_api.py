from __future__ import annotations

import os

import httpx


class AutomationApiClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @classmethod
    def from_env(cls) -> "AutomationApiClient":
        return cls(
            base_url=os.getenv("AUTOMATION_API_BASE_URL", "http://automation-api:8088"),
            api_key=os.getenv("AUTOMATION_WORKER_API_KEY", ""),
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Automation-Api-Key": self.api_key}

    def list_tasks(self) -> list[dict]:
        response = httpx.get(f"{self.base_url}/tasks", headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def queue_run(self, task_id: str) -> dict:
        response = httpx.post(f"{self.base_url}/tasks/{task_id}/run", headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()
