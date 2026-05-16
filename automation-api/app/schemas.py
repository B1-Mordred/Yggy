from __future__ import annotations

import re
from enum import Enum
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\\-]{2,127}$")


class ApprovalLevel(str, Enum):
    L0_READ_ONLY = "L0_READ_ONLY"
    L1_NOTIFY_ONLY = "L1_NOTIFY_ONLY"
    L2_LOCAL_WRITE = "L2_LOCAL_WRITE"
    L3_EXTERNAL_SIDE_EFFECT = "L3_EXTERNAL_SIDE_EFFECT"
    L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE = "L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE"


APPROVAL_ORDER = {
    ApprovalLevel.L0_READ_ONLY: 0,
    ApprovalLevel.L1_NOTIFY_ONLY: 1,
    ApprovalLevel.L2_LOCAL_WRITE: 2,
    ApprovalLevel.L3_EXTERNAL_SIDE_EFFECT: 3,
    ApprovalLevel.L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE: 4,
}


def approval_at_least(level: ApprovalLevel, minimum: ApprovalLevel) -> bool:
    return APPROVAL_ORDER[level] >= APPROVAL_ORDER[minimum]


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "schedule"
    cron: str
    timezone: str = "Europe/Berlin"

    @field_validator("cron")
    @classmethod
    def cron_must_be_valid(cls, value: str) -> str:
        if not croniter.is_valid(value):
            raise ValueError("cron expression is invalid")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone is invalid") from exc
        return value


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    url: str | None = None
    query: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "SourceConfig":
        if self.type in {"rss", "http"}:
            if not self.url:
                raise ValueError(f"{self.type} source requires url")
            parsed = urlparse(self.url)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("source URL scheme must be http or https")
        if self.type == "web_query" and not self.query:
            raise ValueError("web_query source requires query")
        return self


class CheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    name: str
    url: str
    expected_status: int | None = Field(default=None, ge=100, le=599)
    max_age_seconds: int | None = Field(default=None, ge=1, le=86400)

    @field_validator("url")
    @classmethod
    def check_url_scheme(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("check URL scheme must be http or https")
        return value


class FiltersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str
    target: str
    format: str


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_level: ApprovalLevel
    max_items: int = Field(default=10, ge=1, le=100)
    require_sources: bool = True
    allow_external_side_effects: bool = False
    allow_shell: bool = False
    allow_docker_socket: bool = False
    allow_filesystem_write: bool = False


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    retry_count: int = Field(default=1, ge=0, le=10)


class TaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: str
    enabled: bool = False
    owner: str = "local_user"
    created_by: str = "yggdrasil"
    trigger: TriggerConfig
    sources: list[SourceConfig] = Field(default_factory=list)
    checks: list[CheckConfig] = Field(default_factory=list)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    output: OutputConfig
    policy: PolicyConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("id must be slug-like")
        return value


class TopicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    enabled: bool = False
    owner: str = "local_user"
    created_by: str = "yggdrasil"
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    sources: list[SourceConfig] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("id must be slug-like")
        return value


class ApprovalDecision(BaseModel):
    nonce: str


class NotificationRequest(BaseModel):
    target: str
    content: str
    dry_run: bool | None = None


class RunLogCreate(BaseModel):
    task_id: str
    status: str
    log: dict[str, Any] = Field(default_factory=dict)


class RunUpdate(BaseModel):
    status: str
    log: dict[str, Any] = Field(default_factory=dict)
    completed: bool = True


class TaskRunRequest(BaseModel):
    force: bool = False


class HeartbeatUpdate(BaseModel):
    service: str = Field(default="automation-worker", pattern=r"^[a-z0-9][a-z0-9_.-]{1,127}$")
    status: str = Field(default="ok", pattern=r"^[a-z_]{2,32}$")
    detail: dict[str, Any] = Field(default_factory=dict)


class RetentionRequest(BaseModel):
    dry_run: bool = False
    run_retention_days: int | None = Field(default=None, ge=1, le=3650)
    audit_retention_days: int | None = Field(default=None, ge=1, le=3650)
    temp_task_retention_hours: int | None = Field(default=None, ge=0, le=87600)
