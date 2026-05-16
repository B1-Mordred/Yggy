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

    def get_task(self, task_id: str) -> dict:
        response = httpx.get(f"{self.base_url}/tasks/{task_id}", headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def list_runs(self) -> list[dict]:
        response = httpx.get(f"{self.base_url}/runs", headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def send_heartbeat(self, status: str = "ok", detail: dict | None = None) -> dict:
        response = httpx.post(
            f"{self.base_url}/health/heartbeat",
            headers=self.headers,
            json={"service": "automation-worker", "status": status, "detail": detail or {}},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def run_retention(self) -> dict:
        response = httpx.post(f"{self.base_url}/maintenance/retention", headers=self.headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def queue_run(self, task_id: str) -> dict:
        response = httpx.post(f"{self.base_url}/tasks/{task_id}/run", headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def claim_run(self, run_id: str) -> dict | None:
        response = httpx.post(f"{self.base_url}/runs/{run_id}/claim", headers=self.headers, timeout=10)
        if response.status_code == 409:
            return None
        response.raise_for_status()
        return response.json()

    def complete_run(self, run_id: str, status: str, log: dict) -> dict:
        response = httpx.patch(
            f"{self.base_url}/runs/{run_id}",
            headers=self.headers,
            json={"status": status, "log": log, "completed": True},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def send_discord(self, target: str, content: str, dry_run: bool) -> dict:
        response = httpx.post(
            f"{self.base_url}/notifications/discord/send",
            headers=self.headers,
            json={"target": target, "content": content, "dry_run": dry_run},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
