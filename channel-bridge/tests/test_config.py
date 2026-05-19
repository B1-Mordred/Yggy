from __future__ import annotations

from pathlib import Path

import pytest

from bridge.config import (
    BridgeSettings,
    load_channel_configs,
    resolve_discord_channel,
    split_discord_reply,
)


def write_channels(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "channels.yaml").write_text(
        """
version: 1
channels:
  - id: discord_home
    type: discord
    enabled: true
    audience: local_user
    channel_id_ref: DISCORD_HOME_CHANNEL
    allowed_user_ids_ref: DISCORD_ALLOWED_USER_IDS
    allowed_capabilities:
      - chat
      - context
      - memory
    allow_approvals: false
    max_message_chars: 3000
    strip_mentions: true
    reject_attachments: true
  - id: discord_dm_primary
    type: discord_dm
    enabled: true
    audience: local_user
    allowed_user_ids_ref: DISCORD_ALLOWED_USER_IDS
    allowed_capabilities:
      - chat
      - context
      - memory
    allow_approvals: false
    max_message_chars: 3000
    strip_mentions: true
    reject_attachments: true
""".lstrip(),
        encoding="utf-8",
    )


def env_map(values: dict[str, str]):
    return values.get


def test_load_and_resolve_discord_channel(tmp_path):
    write_channels(tmp_path)
    channels = load_channel_configs(tmp_path)

    channel = resolve_discord_channel(
        channels,
        channel_id="channel-1",
        author_id="user-1",
        env=env_map({"DISCORD_HOME_CHANNEL": "channel-1", "DISCORD_ALLOWED_USER_IDS": "user-1,user-2"}),
    )

    assert channel.id == "discord_home"
    assert channel.audience == "local_user"


def test_resolve_discord_dm_channel_uses_allowed_user_without_channel_match(tmp_path):
    write_channels(tmp_path)
    channels = load_channel_configs(tmp_path)

    channel = resolve_discord_channel(
        channels,
        channel_id="discord-generated-dm-channel",
        author_id="user-2",
        is_dm=True,
        env=env_map({"DISCORD_HOME_CHANNEL": "channel-1", "DISCORD_ALLOWED_USER_IDS": "user-1,user-2"}),
    )

    assert channel.id == "discord_dm_primary"
    assert channel.type == "discord_dm"


def test_resolve_discord_channel_rejects_unknown_channel_and_user(tmp_path):
    write_channels(tmp_path)
    channels = load_channel_configs(tmp_path)

    with pytest.raises(LookupError):
        resolve_discord_channel(
            channels,
            channel_id="other-channel",
            author_id="user-1",
            env=env_map({"DISCORD_HOME_CHANNEL": "channel-1", "DISCORD_ALLOWED_USER_IDS": "user-1"}),
        )
    with pytest.raises(PermissionError):
        resolve_discord_channel(
            channels,
            channel_id="channel-1",
            author_id="user-3",
            env=env_map({"DISCORD_HOME_CHANNEL": "channel-1", "DISCORD_ALLOWED_USER_IDS": "user-1"}),
        )
    with pytest.raises(PermissionError):
        resolve_discord_channel(
            channels,
            channel_id="discord-generated-dm-channel",
            author_id="user-3",
            is_dm=True,
            env=env_map({"DISCORD_HOME_CHANNEL": "channel-1", "DISCORD_ALLOWED_USER_IDS": "user-1"}),
        )


def test_placeholder_allowed_users_means_channel_only_restriction(tmp_path):
    write_channels(tmp_path)
    channels = load_channel_configs(tmp_path)

    channel = resolve_discord_channel(
        channels,
        channel_id="channel-1",
        author_id="any-user",
        env=env_map({"DISCORD_HOME_CHANNEL": "channel-1", "DISCORD_ALLOWED_USER_IDS": "replace-with-comma-separated-discord-user-ids-or-empty"}),
    )

    assert channel.id == "discord_home"

    with pytest.raises(PermissionError):
        resolve_discord_channel(
            channels,
            channel_id="discord-generated-dm-channel",
            author_id="any-user",
            is_dm=True,
            env=env_map({"DISCORD_HOME_CHANNEL": "channel-1", "DISCORD_ALLOWED_USER_IDS": "replace-with-comma-separated-discord-user-ids-or-empty"}),
        )


def test_bridge_settings_from_env_clamps_values():
    settings = BridgeSettings.from_env(
        env_map(
            {
                "CHANNEL_BRIDGE_CONFIG_ROOT": "/tmp/configs",
                "CHANNEL_BRIDGE_BRAGI_BASE_URL": "http://bragi:8650/",
                "CHANNEL_BRIDGE_BRAGI_API_KEY": "test-key",
                "CHANNEL_BRIDGE_AUTOMATION_API_BASE_URL": "http://automation-api:8088/",
                "CHANNEL_BRIDGE_AUTOMATION_API_KEY": "audit-key",
                "DISCORD_BOT_TOKEN": "bot-token",
                "CHANNEL_BRIDGE_DISCORD_HISTORY_LIMIT": "999",
                "CHANNEL_BRIDGE_DISCORD_REPLY_LIMIT": "99999",
                "CHANNEL_BRIDGE_FOLLOWUP_POLL_SECONDS": "1",
                "CHANNEL_BRIDGE_FOLLOWUP_LIMIT": "999",
            }
        )
    )

    assert str(settings.config_root) == "/tmp/configs"
    assert settings.bragi_base_url == "http://bragi:8650"
    assert settings.automation_api_base_url == "http://automation-api:8088"
    assert settings.automation_api_key == "audit-key"
    assert settings.discord_history_limit == 20
    assert settings.discord_reply_limit == 2000
    assert settings.followups_enabled is True
    assert settings.followup_poll_seconds == 30
    assert settings.followup_limit == 20


def test_split_discord_reply_respects_limit():
    chunks = split_discord_reply("alpha beta gamma delta", limit=10)

    assert chunks == ["alpha beta", "gamma", "delta"]
    assert all(len(chunk) <= 10 for chunk in chunks)
