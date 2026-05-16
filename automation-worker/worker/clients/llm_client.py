from __future__ import annotations

import os

import httpx


class OllamaSummarizer:
    def __init__(self, base_url: str | None = None, model: str | None = None, enabled: bool | None = None) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")).rstrip("/")
        self.model = model or os.getenv("LLM_SUMMARIZER_MODEL", "llama3.1")
        self.enabled = enabled if enabled is not None else os.getenv("LLM_SUMMARIZER_ENABLED", "false").lower() == "true"

    def summarize(self, prompt: str) -> str:
        if not self.enabled:
            return prompt[:1000]
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        response.raise_for_status()
        return response.json().get("response", "")
