from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .goal_models import AutomationRequestClassification
from .goal_prompts import HERMES_CLARIFIER_SYSTEM_PROMPT, hermes_clarifier_user_prompt


SECRET_KEY_RE = re.compile(
    r"(?i)\b(api[_ -]?key|token|password|secret|private[_ -]?key|cookie|nonce|authorization)\b"
    r"\s*(?:is|=|:)?\s*([^\s,;]+)?"
)
URL_RE = re.compile(r"https?://[^\s<>)\"']+")
DISCORD_WEBHOOK_RE = re.compile(r"https://discord(?:app)?\.com/api/webhooks/\S+", re.IGNORECASE)
TOKEN_SHAPE_RE = re.compile(r"\b(?:sk|ghp|gho|ghu|github_pat|xoxb|xoxp)[-_][A-Za-z0-9_=-]{8,}\b")


class HermesClarifierError(RuntimeError):
    pass


def redact_for_hermes(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(marker in lowered for marker in ("authorization", "cookie", "password", "secret", "token", "api_key", "apikey", "private_key", "credential", "nonce")):
                redacted[key_text] = "[redacted]"
            elif lowered in {"url", "webhook_url", "path"}:
                redacted[key_text] = "[omitted]"
            else:
                redacted[key_text] = redact_for_hermes(item)
        return redacted
    if isinstance(value, list):
        return [redact_for_hermes(item) for item in value[:50]]
    if isinstance(value, str):
        return redact_hermes_text(value)
    return value


def redact_hermes_text(text: str) -> str:
    redacted = DISCORD_WEBHOOK_RE.sub("[redacted-discord-webhook]", str(text))
    redacted = URL_RE.sub("[redacted-url]", redacted)
    redacted = TOKEN_SHAPE_RE.sub("[redacted-token]", redacted)
    redacted = SECRET_KEY_RE.sub(lambda match: f"{match.group(1)}=[redacted]", redacted)
    return redacted[:4000]


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if not stripped:
        raise HermesClarifierError("empty Hermes response")
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return value

    start = stripped.find("{")
    if start < 0:
        raise HermesClarifierError("Hermes response did not contain a JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(stripped[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : index + 1]
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict):
                    raise HermesClarifierError("Hermes JSON was not an object")
                return parsed
    raise HermesClarifierError("Hermes JSON object was incomplete")


class HermesClarifierClient:
    def __init__(self, *, base_url: str, model: str, timeout: float = 30.0, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.api_key = api_key.strip()

    def classify_request(
        self,
        *,
        user_text: str,
        visible_tasks: list[dict[str, Any]],
        task_aliases: dict[str, str],
        capability_ids: list[str],
        deterministic_classification: AutomationRequestClassification,
    ) -> AutomationRequestClassification:
        if not self.base_url:
            raise HermesClarifierError("Hermes base URL is not configured")
        safe_user_text = redact_hermes_text(user_text)
        safe_tasks = redact_for_hermes(visible_tasks)
        safe_aliases = redact_for_hermes(task_aliases)
        safe_deterministic = redact_for_hermes(deterministic_classification.model_dump(mode="json"))
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": HERMES_CLARIFIER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": hermes_clarifier_user_prompt(
                        user_text=safe_user_text,
                        visible_tasks=safe_tasks if isinstance(safe_tasks, list) else [],
                        task_aliases=safe_aliases if isinstance(safe_aliases, dict) else {},
                        capability_ids=capability_ids,
                        deterministic_classification=safe_deterministic if isinstance(safe_deterministic, dict) else {},
                    ),
                },
            ],
            "temperature": 0,
            "stream": False,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
        }
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/v1/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            raw = response.json()
        except Exception as exc:
            raise HermesClarifierError(f"Hermes clarifier request failed: {exc.__class__.__name__}") from exc

        content = ""
        if isinstance(raw, dict):
            choices = raw.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    content = message["content"]
            elif isinstance(raw.get("content"), str):
                content = raw["content"]
        parsed = normalize_classification_payload(extract_json_object(content))
        return AutomationRequestClassification.model_validate(parsed)


def normalize_classification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if normalized.get("reason") is None:
        normalized["reason"] = ""
    if normalized.get("confidence") is None:
        normalized["confidence"] = 0.0
    for list_key in ("target_task_candidates", "missing_information", "assumptions", "unsafe_reasons"):
        if not isinstance(normalized.get(list_key), list):
            normalized[list_key] = []
    if normalized.get("operation") is not None and not isinstance(normalized.get("operation"), dict):
        normalized["operation"] = None
    if normalized.get("candidate_intent") is not None and not isinstance(normalized.get("candidate_intent"), dict):
        normalized["candidate_intent"] = None
    operation = normalized.get("operation")
    if isinstance(operation, dict) and not normalized.get("target_task_id"):
        task_id = operation.get("task_id")
        if isinstance(task_id, str) and task_id:
            normalized["target_task_id"] = task_id
    return normalized
