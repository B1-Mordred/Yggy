from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .goal_loop import classify_automation_request
from .goal_models import AutomationRequestClassification


HOST = os.getenv("BRAGI_CLARIFIER_HOST", "127.0.0.1")
PORT = int(os.getenv("BRAGI_CLARIFIER_PORT", "8651"))
API_KEY = os.getenv("BRAGI_CLARIFIER_API_KEY", os.getenv("API_SERVER_KEY", "")).strip()
MODEL_NAME = os.getenv("BRAGI_CLARIFIER_MODEL_NAME", "bragi-clarifier")
MAX_CANDIDATES = int(os.getenv("BRAGI_CLARIFIER_MAX_CANDIDATES", "5"))


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content or "")


def extract_prompt_payload(messages: list[dict[str, Any]]) -> dict[str, Any]:
    text = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            text = extract_text(message.get("content"))
            break
    if not text:
        return {}
    start = text.find("{")
    if start < 0:
        return {"latest_user_request": text}
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start=start):
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
                try:
                    parsed = json.loads(text[start : index + 1])
                    return parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    return {"latest_user_request": text}
    return {"latest_user_request": text}


def clean_aliases(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    aliases: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key).strip().lower()
        item_text = str(item).strip()
        if key_text and re.fullmatch(r"[a-z][a-z0-9_]{2,127}", item_text):
            aliases[key_text] = item_text
    return aliases


def clean_visible_tasks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    tasks: list[dict[str, Any]] = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,127}", task_id):
            continue
        tasks.append(
            {
                "id": task_id,
                **({"name": str(item["name"])[:200]} if item.get("name") else {}),
                **({"type": str(item["type"])[:80]} if item.get("type") else {}),
                **({"enabled": bool(item["enabled"])} if "enabled" in item else {}),
            }
        )
    return tasks


def classify_from_messages(messages: list[dict[str, Any]]) -> AutomationRequestClassification:
    payload = extract_prompt_payload(messages)
    user_text = str(payload.get("latest_user_request") or "").strip()
    return classify_automation_request(
        user_text,
        visible_tasks=clean_visible_tasks(payload.get("visible_tasks")),
        task_aliases=clean_aliases(payload.get("task_aliases")),
        max_candidates=MAX_CANDIDATES,
        use_hermes=False,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "BragiClarifierAPI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def authorized(self) -> bool:
        if not API_KEY:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {API_KEY}"

    def do_GET(self) -> None:
        if self.path == "/health":
            json_response(self, 200, {"status": "ok", "service": "bragi-clarifier", "model": MODEL_NAME})
            return
        if self.path == "/v1/models":
            if not self.authorized():
                json_response(self, 401, {"error": {"message": "unauthorized"}})
                return
            json_response(
                self,
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL_NAME,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "hermes",
                            "permission": [],
                            "root": MODEL_NAME,
                            "parent": None,
                        }
                    ],
                },
            )
            return
        json_response(self, 404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            json_response(self, 404, {"error": {"message": "not found"}})
            return
        if not self.authorized():
            json_response(self, 401, {"error": {"message": "unauthorized"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            messages = payload.get("messages") or []
            if not isinstance(messages, list):
                raise ValueError("messages must be a list")
            classification = classify_from_messages(messages).model_dump(mode="json")
            created = int(time.time())
            json_response(
                self,
                200,
                {
                    "id": f"chatcmpl-bragi-clarifier-{created}",
                    "object": "chat.completion",
                    "created": created,
                    "model": str(payload.get("model") or MODEL_NAME),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": json.dumps(classification)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )
        except Exception as exc:
            json_response(self, 500, {"error": {"message": str(exc), "type": exc.__class__.__name__}})


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"bragi clarifier listening on {HOST}:{PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
