from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_host: str = "0.0.0.0"
    api_port: int = 8088
    tool_api_key: str = ""
    admin_api_key: str = ""
    worker_api_key: str = ""
    database_url: str = "sqlite+pysqlite:///:memory:"
    policy_file: str = "configs/policies.yaml"
    discord_dry_run: bool = True
    discord_webhook_briefings: str = ""
    discord_webhook_alerts: str = ""
    discord_webhook_approvals: str = ""
    version: str = "0.1.0"


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    return Settings(
        api_host=os.getenv("AUTOMATION_API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("AUTOMATION_API_PORT", "8088")),
        tool_api_key=os.getenv("AUTOMATION_TOOL_API_KEY", ""),
        admin_api_key=os.getenv("AUTOMATION_ADMIN_API_KEY", ""),
        worker_api_key=os.getenv("AUTOMATION_WORKER_API_KEY", ""),
        database_url=os.getenv("DATABASE_URL", "sqlite+pysqlite:///:memory:"),
        policy_file=os.getenv("AUTOMATION_POLICY_FILE", "configs/policies.yaml"),
        discord_dry_run=env_bool("DISCORD_DRY_RUN", True),
        discord_webhook_briefings=os.getenv("DISCORD_WEBHOOK_BRIEFINGS", ""),
        discord_webhook_alerts=os.getenv("DISCORD_WEBHOOK_ALERTS", ""),
        discord_webhook_approvals=os.getenv("DISCORD_WEBHOOK_APPROVALS", ""),
    )
