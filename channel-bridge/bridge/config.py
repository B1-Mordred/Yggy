from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any

import yaml


EnvResolver = Callable[[str], str | None]


SAFE_CHANNEL_CAPABILITIES = {
    "chat",
    "context",
    "memory",
    "source_proposal",
    "draft_task",
    "task_read",
    "run_l1",
    "pause_l1",
}


@dataclass(frozen=True)
class ChannelConfig:
    id: str
    type: str
    enabled: bool
    audience: str
    allowed_capabilities: tuple[str, ...] = field(default_factory=tuple)
    allow_approvals: bool = False
    max_message_chars: int = 3000
    channel_id_ref: str = ""
    allowed_user_ids_ref: str = ""
    strip_mentions: bool = True
    reject_attachments: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChannelConfig":
        return cls(
            id=str(data.get("id") or ""),
            type=str(data.get("type") or ""),
            enabled=bool(data.get("enabled", True)),
            audience=str(data.get("audience") or "local_user"),
            allowed_capabilities=tuple(str(item) for item in data.get("allowed_capabilities") or ()),
            allow_approvals=bool(data.get("allow_approvals", False)),
            max_message_chars=int(data.get("max_message_chars") or 3000),
            channel_id_ref=str(data.get("channel_id_ref") or ""),
            allowed_user_ids_ref=str(data.get("allowed_user_ids_ref") or ""),
            strip_mentions=bool(data.get("strip_mentions", True)),
            reject_attachments=bool(data.get("reject_attachments", True)),
        )


@dataclass(frozen=True)
class BridgeSettings:
    config_root: Path
    bragi_base_url: str
    bragi_api_key: str
    automation_api_base_url: str
    automation_api_key: str
    discord_bot_token: str
    discord_enabled: bool = True
    discord_history_limit: int = 8
    discord_reply_limit: int = 1900
    discord_nickname: str = "Bragi"
    followups_enabled: bool = True
    followup_poll_seconds: int = 300
    followup_limit: int = 5
    notifications_enabled: bool = True
    notification_limit: int = 10
    http_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, env: EnvResolver | None = None) -> "BridgeSettings":
        resolver = env or os.getenv
        return cls(
            config_root=Path(resolver("CHANNEL_BRIDGE_CONFIG_ROOT") or "/app/configs"),
            bragi_base_url=(resolver("CHANNEL_BRIDGE_BRAGI_BASE_URL") or "http://bragi:8650").rstrip("/"),
            bragi_api_key=resolver("CHANNEL_BRIDGE_BRAGI_API_KEY") or resolver("BRAGI_API_KEY") or "",
            automation_api_base_url=(resolver("CHANNEL_BRIDGE_AUTOMATION_API_BASE_URL") or "http://automation-api:8088").rstrip("/"),
            automation_api_key=resolver("CHANNEL_BRIDGE_AUTOMATION_API_KEY") or resolver("AUTOMATION_CHANNEL_BRIDGE_API_KEY") or "",
            discord_bot_token=resolver("DISCORD_BOT_TOKEN") or "",
            discord_enabled=env_bool(resolver("CHANNEL_BRIDGE_DISCORD_ENABLED"), True),
            discord_history_limit=clamp_int(resolver("CHANNEL_BRIDGE_DISCORD_HISTORY_LIMIT"), default=8, minimum=0, maximum=20),
            discord_reply_limit=clamp_int(resolver("CHANNEL_BRIDGE_DISCORD_REPLY_LIMIT"), default=1900, minimum=200, maximum=2000),
            discord_nickname=resolver("CHANNEL_BRIDGE_DISCORD_NICKNAME") or "Bragi",
            followups_enabled=env_bool(resolver("CHANNEL_BRIDGE_FOLLOWUPS_ENABLED"), True),
            followup_poll_seconds=clamp_int(resolver("CHANNEL_BRIDGE_FOLLOWUP_POLL_SECONDS"), default=300, minimum=30, maximum=86400),
            followup_limit=clamp_int(resolver("CHANNEL_BRIDGE_FOLLOWUP_LIMIT"), default=5, minimum=1, maximum=20),
            notifications_enabled=env_bool(resolver("CHANNEL_BRIDGE_NOTIFICATIONS_ENABLED"), True),
            notification_limit=clamp_int(resolver("CHANNEL_BRIDGE_NOTIFICATION_LIMIT"), default=10, minimum=1, maximum=50),
            http_timeout_seconds=float(resolver("CHANNEL_BRIDGE_HTTP_TIMEOUT") or "30"),
        )


def env_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def clamp_int(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value)) if value not in {None, ""} else default
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def load_channel_configs(config_root: Path | str) -> list[ChannelConfig]:
    path = Path(config_root) / "channels.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    channels = data.get("channels")
    if not isinstance(channels, list):
        return []
    return [ChannelConfig.from_dict(item) for item in channels if isinstance(item, dict)]


def is_placeholder_value(value: str | None) -> bool:
    if value is None:
        return True
    stripped = value.strip().lower()
    return not stripped or stripped.startswith("replace-with") or stripped in {"changeme", "todo", "unset"}


def env_ref_value(ref: str, env: EnvResolver | None = None) -> str:
    if not ref:
        return ""
    resolver = env or os.getenv
    return (resolver(ref) or "").strip()


def comma_env_values(ref: str, env: EnvResolver | None = None) -> set[str]:
    raw = env_ref_value(ref, env)
    if is_placeholder_value(raw):
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def resolve_discord_channel(
    channels: list[ChannelConfig],
    *,
    channel_id: str,
    author_id: str,
    is_dm: bool = False,
    env: EnvResolver | None = None,
) -> ChannelConfig:
    dm_channel_seen = False
    for channel in channels:
        if not channel.enabled:
            continue
        if is_dm:
            if channel.type != "discord_dm":
                continue
            dm_channel_seen = True
        elif channel.type != "discord":
            continue
        allowed_user_ids = comma_env_values(channel.allowed_user_ids_ref, env)
        if is_dm:
            if not allowed_user_ids:
                continue
            if author_id not in allowed_user_ids:
                continue
            return channel
        configured_channel_id = env_ref_value(channel.channel_id_ref, env)
        if is_placeholder_value(configured_channel_id) or configured_channel_id != channel_id:
            continue
        if allowed_user_ids and author_id not in allowed_user_ids:
            raise PermissionError("discord author is not allowed for this channel")
        return channel
    if is_dm and dm_channel_seen:
        raise PermissionError("discord dm author is not allowed or no explicit dm user list is configured")
    raise LookupError("discord channel is not registered for Bragi")


def split_discord_reply(reply: str, *, limit: int = 1900) -> list[str]:
    limit = max(5, min(2000, limit))
    text = reply.strip()
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
