from __future__ import annotations

import re
from typing import Any


SECRET_KEY_MARKERS = ("password", "secret", "token", "api_key", "apikey", "private_key", "cookie", "webhook")
NON_SECRET_KEY_EXACT = {"webhook_id"}
SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"https://(?:canary\\.)?discord(?:app)?\\.com/api/webhooks/\\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|token|secret)\\s*[:=]\\s*\\S+"),
]


def find_secret_paths(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            key_lower = str(key).lower()
            if _key_is_secret_like(key_lower) and _has_plain_value(child):
                findings.append(child_path)
            findings.extend(find_secret_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_secret_paths(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                findings.append(path)
                break
    return findings


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            key_lower = str(key).lower()
            if _key_is_secret_like(key_lower) and _has_plain_value(child):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_secrets(child)
        return result
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in SECRET_VALUE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    return value


def _has_plain_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float, bool)):
        return True
    if isinstance(value, list):
        return any(_has_plain_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_plain_value(item) for item in value.values())
    return True


def _key_is_secret_like(key_lower: str) -> bool:
    if key_lower in NON_SECRET_KEY_EXACT:
        return False
    return any(marker in key_lower for marker in SECRET_KEY_MARKERS)
