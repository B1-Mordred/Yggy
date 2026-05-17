from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_bragi_compose_environment_is_minimal():
    compose = yaml.safe_load((ROOT / "docker-compose.automation.yml").read_text(encoding="utf-8"))
    bragi = compose["services"]["bragi"]
    environment = bragi.get("environment") or {}

    assert "env_file" not in bragi
    assert "AUTOMATION_TOOL_API_KEY" in environment
    assert "BRAGI_API_KEY" in environment
    assert "BRAGI_YGGDRASIL_API_KEY" in environment

    forbidden = {
        "AUTOMATION_ADMIN_API_KEY",
        "AUTOMATION_WORKER_API_KEY",
        "DATABASE_URL",
        "DISCORD_BOT_TOKEN",
        "DISCORD_WEBHOOK_BRIEFINGS",
        "DISCORD_WEBHOOK_ALERTS",
        "DISCORD_WEBHOOK_APPROVALS",
        "N8N_WEBHOOK_SHARED_SECRET",
        "MYSQL_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
    }
    assert forbidden.isdisjoint(environment)


def test_channel_bridge_compose_environment_is_minimal():
    compose = yaml.safe_load((ROOT / "docker-compose.automation.yml").read_text(encoding="utf-8"))
    bridge = compose["services"]["channel-bridge"]
    environment = bridge.get("environment") or {}

    assert "env_file" not in bridge
    assert "DISCORD_BOT_TOKEN" in environment
    assert "CHANNEL_BRIDGE_BRAGI_API_KEY" in environment
    assert "DISCORD_HOME_CHANNEL" in environment
    assert "DISCORD_ALLOWED_USER_IDS" in environment

    forbidden = {
        "AUTOMATION_ADMIN_API_KEY",
        "AUTOMATION_WORKER_API_KEY",
        "DATABASE_URL",
        "DISCORD_WEBHOOK_BRIEFINGS",
        "DISCORD_WEBHOOK_ALERTS",
        "DISCORD_WEBHOOK_APPROVALS",
        "N8N_WEBHOOK_SHARED_SECRET",
        "MYSQL_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
    }
    assert forbidden.isdisjoint(environment)


def test_lan_compose_exposes_bragi_only_with_explicit_host():
    lan = yaml.safe_load((ROOT / "docker-compose.lan.yml").read_text(encoding="utf-8"))
    bragi_ports = lan["services"]["bragi"]["ports"]

    assert any("BRAGI_LAN_PUBLISHED_HOST:?" in port for port in bragi_ports)
    assert any("BRAGI_LAN_PUBLISHED_PORT:-8650" in port for port in bragi_ports)
