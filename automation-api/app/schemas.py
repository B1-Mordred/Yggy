from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal
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

    source_id: str | None = None
    type: str
    url: str | None = None
    query: str | None = None

    @field_validator("source_id")
    @classmethod
    def source_id_must_be_slug(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_RE.match(value):
            raise ValueError("source_id must be slug-like")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "SourceConfig":
        if self.type not in {"rss", "http", "web_query"}:
            raise ValueError("source type must be rss, http, or web_query")
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
    max_runs_per_hour: int | None = Field(default=12, ge=1, le=1000)
    max_runs_per_day: int | None = Field(default=50, ge=1, le=10000)
    min_seconds_between_runs: int = Field(default=0, ge=0, le=86400)
    allow_external_side_effects: bool = False
    allow_shell: bool = False
    allow_docker_socket: bool = False
    allow_filesystem_write: bool = False


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    retry_count: int = Field(default=1, ge=0, le=10)


class TaskTemplateRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    cron: str | None = None
    timezone: str | None = None
    output_target: str | None = None
    source_ids: list[str] | None = None
    check_ids: list[str] | None = None
    webhook_id: str | None = None
    n8n_payload: dict[str, Any] | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None
    max_items: int | None = Field(default=None, ge=1, le=100)
    owner: str | None = None
    created_by: str | None = None

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("id must be slug-like")
        return value

    @field_validator("source_ids")
    @classmethod
    def source_ids_must_be_slug_like(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for source_id in value:
            if not SLUG_RE.match(source_id):
                raise ValueError("source_ids must be slug-like")
        return value

    @field_validator("check_ids")
    @classmethod
    def check_ids_must_be_slug_like(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for check_id in value:
            if not re.match(r"^[a-z0-9][a-z0-9_.-]{1,127}$", check_id):
                raise ValueError("check_ids must be slug-like")
        return value

    @field_validator("webhook_id")
    @classmethod
    def template_webhook_id_must_be_slug_like(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_RE.match(value):
            raise ValueError("webhook_id must be slug-like")
        return value


class TaskTemplateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    task_type: str
    default_approval_level: ApprovalLevel
    allowed_output_targets: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    example_prompts: list[str] = Field(default_factory=list)


class GatewayOutcome(str, Enum):
    ACCEPT = "ACCEPT"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    REJECT_UNSUPPORTED = "REJECT_UNSUPPORTED"
    REJECT_UNSAFE = "REJECT_UNSAFE"
    PROPOSE_NEW_CAPABILITY = "PROPOSE_NEW_CAPABILITY"


class CanonicalIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["draft_task"] = "draft_task"
    capability_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_user_confirmation: bool = True
    user_confirmation_obtained: bool = False
    slots: dict[str, Any] = Field(default_factory=dict)
    user_request: str | None = Field(default=None, max_length=4000)

    @field_validator("capability_id")
    @classmethod
    def capability_id_must_be_versioned(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*\.v[0-9]+$", value):
            raise ValueError("capability_id must look like name.v1")
        return value


class CapabilityGatewayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: GatewayOutcome
    capability_id: str | None = None
    message: str
    missing_slots: list[str] = Field(default_factory=list)
    unsafe_reasons: list[str] = Field(default_factory=list)
    confirmation_summary: dict[str, Any] | None = None
    yggdrasil_request: dict[str, Any] | None = None


class QuietHoursConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"
    timezone: str = "Europe/Berlin"

    @field_validator("start", "end")
    @classmethod
    def time_must_be_hhmm(cls, value: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", value):
            raise ValueError("quiet hour time must be HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone is invalid") from exc
        return value


class NotificationPreferencesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_success: bool = True
    on_failure: bool = True
    on_empty_result: bool = False
    quiet_hours: QuietHoursConfig = Field(default_factory=QuietHoursConfig)
    collapse_repeated_failures: bool = True
    failure_collapse_window_minutes: int = Field(default=360, ge=1, le=10080)


class N8nWebhookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    webhook_id: str | None = None
    path: str | None = None
    method: str = "POST"
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("webhook_id")
    @classmethod
    def webhook_id_must_be_slug(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_RE.match(value):
            raise ValueError("webhook_id must be slug-like")
        return value

    @field_validator("method")
    @classmethod
    def method_must_be_post(cls, value: str) -> str:
        if value.upper() != "POST":
            raise ValueError("n8n webhook method must be POST")
        return value.upper()

    @field_validator("path")
    @classmethod
    def path_must_be_internal_webhook_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.startswith(("/webhook/", "/webhook-test/")):
            raise ValueError("n8n webhook path must start with /webhook/ or /webhook-test/")
        parsed = urlparse(value)
        if parsed.scheme or parsed.netloc:
            raise ValueError("n8n webhook path must not be an absolute URL")
        return value


class BackupVerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backup_root: str = "/app/backups"
    max_age_hours: int = Field(default=26, ge=1, le=720)
    min_mysql_dump_bytes: int = Field(default=1024, ge=1, le=10_000_000_000)
    secret_scan_enabled: bool = True
    max_scan_bytes_per_file: int = Field(default=2_000_000, ge=1024, le=25_000_000)
    required_files: list[str] = Field(
        default_factory=lambda: [
            "manifest.json",
            "mysql/automation.sql",
            "api/health.json",
            "api/tasks.json",
            "api/topics.json",
            "api/openapi.json",
            "git-commit.txt",
        ]
    )

    @field_validator("backup_root")
    @classmethod
    def backup_root_must_be_worker_backup_mount(cls, value: str) -> str:
        normalized = value.rstrip("/") or value
        if not normalized.startswith("/app/backups"):
            raise ValueError("backup_root must be under the worker read-only /app/backups mount")
        return normalized

    @field_validator("required_files")
    @classmethod
    def required_files_must_be_relative(cls, value: list[str]) -> list[str]:
        for item in value:
            if item.startswith("/") or ".." in item.split("/"):
                raise ValueError("backup required_files entries must be relative paths")
        return value


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
    notifications: NotificationPreferencesConfig = Field(default_factory=NotificationPreferencesConfig)
    n8n: N8nWebhookConfig | None = None
    backup: BackupVerificationConfig | None = None

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("id must be slug-like")
        return value

    @model_validator(mode="after")
    def validate_type_specific_config(self) -> "TaskConfig":
        if self.type == "n8n_webhook" and self.n8n is None:
            raise ValueError("n8n_webhook task requires n8n config")
        if self.type == "backup_verification" and self.backup is None:
            raise ValueError("backup_verification task requires backup config")
        return self


class TaskChangeProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_config: TaskConfig
    summary: str = Field(default="", max_length=1200)
    requested_by: str = Field(default="yggdrasil", min_length=1, max_length=128)


class TaskChangeProposalReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


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


class ApprovedSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: str
    url: str | None = None
    query: str | None = None
    categories: list[str] = Field(default_factory=list)
    trust_level: str = "approved"
    enabled: bool = True
    max_items: int | None = Field(default=None, ge=1, le=100)

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("id must be slug-like")
        return value

    @field_validator("categories")
    @classmethod
    def categories_must_be_slug_like(cls, value: list[str]) -> list[str]:
        for category in value:
            if not SLUG_RE.match(category):
                raise ValueError("source categories must be slug-like")
        return value

    @field_validator("trust_level")
    @classmethod
    def trust_level_must_be_slug_like(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("source trust_level must be slug-like")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "ApprovedSourceConfig":
        SourceConfig(type=self.type, url=self.url, query=self.query)
        return self


class SourceRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    sources: list[ApprovedSourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registry(self) -> "SourceRegistryConfig":
        if self.version != 1:
            raise ValueError("approved_sources.yaml version must be 1")
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("approved source ids must be unique")
        keys = [_source_identity(source) for source in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("approved source identities must be unique")
        return self


def _source_identity(source: SourceConfig | ApprovedSourceConfig) -> tuple[str, str]:
    if source.type in {"rss", "http"}:
        return source.type, source.url or ""
    if source.type == "web_query":
        return source.type, source.query or ""
    return source.type, source.url or source.query or ""


class ApprovedN8nWebhookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    path: str
    method: str = "POST"
    enabled: bool = True
    max_payload_keys: int = Field(default=20, ge=1, le=100)
    description: str = ""

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("id must be slug-like")
        return value

    @model_validator(mode="after")
    def validate_webhook(self) -> "ApprovedN8nWebhookConfig":
        N8nWebhookConfig(webhook_id=self.id, path=self.path, method=self.method)
        return self


class N8nWebhookRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    webhooks: list[ApprovedN8nWebhookConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registry(self) -> "N8nWebhookRegistryConfig":
        if self.version != 1:
            raise ValueError("n8n_webhooks.yaml version must be 1")
        ids = [webhook.id for webhook in self.webhooks]
        if len(ids) != len(set(ids)):
            raise ValueError("approved n8n webhook ids must be unique")
        paths = [webhook.path for webhook in self.webhooks]
        if len(paths) != len(set(paths)):
            raise ValueError("approved n8n webhook paths must be unique")
        return self


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


class StaleRunRecoveryRequest(BaseModel):
    dry_run: bool = False
    task_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_\-]{2,127}$")
    stale_after_seconds: int | None = Field(default=None, ge=60, le=86400)
    limit: int = Field(default=100, ge=1, le=500)
