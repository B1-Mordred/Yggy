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
    llm_summary_enabled: bool | None = None
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
    printer_ids: list[str] | None = None
    low_threshold_percent: int | None = Field(default=None, ge=1, le=100)
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

    @field_validator("printer_ids")
    @classmethod
    def printer_ids_must_be_slug_like(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for printer_id in value:
            if not SLUG_RE.match(printer_id):
                raise ValueError("printer_ids must be slug-like")
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

    intent: Literal["draft_task", "propose_task_change"] = "draft_task"
    capability_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_user_confirmation: bool = True
    user_confirmation_obtained: bool = False
    slots: dict[str, Any] = Field(default_factory=dict)
    user_request: str | None = Field(default=None, max_length=4000)

    @field_validator("capability_id")
    @classmethod
    def capability_id_must_be_versioned(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.v[0-9]+$", value):
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


class DigestQualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    min_items: int = Field(default=0, ge=0, le=1000)
    min_successful_sources: int | None = Field(default=None, ge=0, le=1000)
    alert_on_source_errors: bool = False
    alert_on_empty_sections: bool = False
    alert_on_delivery_failure: bool = True
    alert_target: str = "alerts"

    @field_validator("alert_target")
    @classmethod
    def alert_target_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("quality alert_target must be slug-like")
        return value


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


class PrinterSupplyEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    printer_id: str
    name: str
    type: Literal["http_json"] = "http_json"
    url: str
    low_threshold_percent: int = Field(default=20, ge=1, le=100)
    expected_status: int = Field(default=200, ge=100, le=599)

    @field_validator("printer_id")
    @classmethod
    def printer_id_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("printer_id must be slug-like")
        return value

    @field_validator("url")
    @classmethod
    def printer_url_must_be_plain_http(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("printer supply URL scheme must be http or https")
        if parsed.username or parsed.password:
            raise ValueError("printer supply URL must not contain credentials")
        return value


class ApprovedPrinterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: Literal["http_json"] = "http_json"
    url: str
    enabled: bool = True
    default_low_threshold_percent: int = Field(default=20, ge=1, le=100)
    expected_status: int = Field(default=200, ge=100, le=599)
    description: str = ""

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("printer id must be slug-like")
        return value

    @field_validator("url")
    @classmethod
    def url_must_be_plain_http(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("printer supply URL scheme must be http or https")
        if parsed.username or parsed.password:
            raise ValueError("printer supply URL must not contain credentials")
        return value

    def to_task_endpoint(self, *, low_threshold_percent: int | None = None) -> PrinterSupplyEndpointConfig:
        return PrinterSupplyEndpointConfig(
            printer_id=self.id,
            name=self.name,
            type=self.type,
            url=self.url,
            low_threshold_percent=low_threshold_percent or self.default_low_threshold_percent,
            expected_status=self.expected_status,
        )


class PrinterRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    printers: list[ApprovedPrinterConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registry(self) -> "PrinterRegistryConfig":
        if self.version != 1:
            raise ValueError("printers.yaml version must be 1")
        ids = [printer.id for printer in self.printers]
        if len(ids) != len(set(ids)):
            raise ValueError("printer ids must be unique")
        urls = [(printer.type, printer.url) for printer in self.printers]
        if len(urls) != len(set(urls)):
            raise ValueError("printer supply endpoints must be unique")
        return self


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
    quality: DigestQualityConfig = Field(default_factory=DigestQualityConfig)
    n8n: N8nWebhookConfig | None = None
    backup: BackupVerificationConfig | None = None
    printer_supplies: list[PrinterSupplyEndpointConfig] = Field(default_factory=list)

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
        if self.type == "printer_supply_status" and not self.printer_supplies:
            raise ValueError("printer_supply_status task requires printer_supplies config")
        return self


class TaskChangeProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_config: TaskConfig
    summary: str = Field(default="", max_length=1200)
    requested_by: str = Field(default="yggdrasil", min_length=1, max_length=128)


class TaskChangeProposalReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class SourceProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: ApprovedSourceConfig
    summary: str = Field(default="", max_length=1200)
    requested_by: str = Field(default="bragi", min_length=1, max_length=128)


class SourceProposalReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class CapabilityProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=255)
    requested_by: str = Field(default="bragi", min_length=1, max_length=128)
    source_channel: str = Field(default="openwebui", min_length=1, max_length=64)
    original_request_preview: str = Field(default="", max_length=1000)
    purpose: str = Field(min_length=10, max_length=2000)
    suggested_capability_id: str = Field(min_length=3, max_length=128)
    suggested_task_type: str = Field(min_length=3, max_length=128)
    likely_approval_level: ApprovalLevel = ApprovalLevel.L1_NOTIFY_ONLY
    required_inputs: list[str] = Field(default_factory=list, max_length=20)
    safety_rules: list[str] = Field(default_factory=list, max_length=30)
    non_goals: list[str] = Field(default_factory=list, max_length=30)
    review_notes: str = Field(default="", max_length=2000)

    @field_validator("suggested_capability_id")
    @classmethod
    def capability_id_must_be_versioned(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.v[0-9]+$", value):
            raise ValueError("suggested_capability_id must look like name.v1")
        return value

    @field_validator("suggested_task_type")
    @classmethod
    def task_type_must_be_slug_like(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("suggested_task_type must be slug-like")
        return value

    @field_validator("required_inputs", "safety_rules", "non_goals")
    @classmethod
    def list_items_must_be_plain(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip()[:300] for item in value if str(item).strip()]
        if len(cleaned) != len(value):
            raise ValueError("proposal list entries may not be empty")
        return cleaned


class CapabilityProposalClose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "rejected", "closed"] = "closed"
    reason: str = Field(default="", max_length=1000)


class CapabilityImplementationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=1000)
    created_by: str = Field(default="local_cli", min_length=1, max_length=128)


class CapabilityImplementationRunUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["queued", "running", "completed", "failed"] | None = None
    branch: str | None = Field(default=None, max_length=255)
    commit_sha: str | None = Field(default=None, max_length=64)
    summary: str | None = Field(default=None, max_length=4000)
    test_results: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=4000)

    @field_validator("branch")
    @classmethod
    def branch_must_be_plain(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if cleaned and not re.match(r"^[A-Za-z0-9._/\-]+$", cleaned):
            raise ValueError("branch contains unsupported characters")
        return cleaned

    @field_validator("commit_sha")
    @classmethod
    def commit_sha_must_be_hex(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if cleaned and not re.match(r"^[0-9a-fA-F]{7,64}$", cleaned):
            raise ValueError("commit_sha must be a git commit hash")
        return cleaned


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
    description: str = ""
    region: str = ""
    languages: list[str] = Field(default_factory=list)
    source_type_label: str = ""
    update_cadence: str = ""
    ingestion_notes: str = ""
    ai_safe_fit: str = ""
    ingestion_mode: str = "feed_metadata"

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

    @field_validator("languages")
    @classmethod
    def languages_must_be_plain(cls, value: list[str]) -> list[str]:
        return [str(item).strip()[:32] for item in value if str(item).strip()]

    @field_validator("ingestion_mode")
    @classmethod
    def ingestion_mode_must_be_known(cls, value: str) -> str:
        if value not in {"feed_metadata", "http_summary", "metadata_only"}:
            raise ValueError("source ingestion_mode must be feed_metadata, http_summary, or metadata_only")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "ApprovedSourceConfig":
        SourceConfig(type=self.type, url=self.url, query=self.query)
        return self


class SourceRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    include_files: list[str] = Field(default_factory=list)
    sources: list[ApprovedSourceConfig] = Field(default_factory=list)

    @field_validator("include_files")
    @classmethod
    def include_files_must_be_plain(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if len(cleaned) != len(value):
            raise ValueError("source registry include_files entries may not be empty")
        for item in cleaned:
            if item.startswith("/") or ".." in item.split("/"):
                raise ValueError("source registry include_files must be relative paths below the registry directory")
            if not item.endswith((".yaml", ".yml", ".tsv")):
                raise ValueError("source registry include_files entries must be yaml/yml/tsv files")
        return cleaned

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


class ChannelEventStatus(str, Enum):
    IGNORED = "ignored"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    FAILED = "failed"
    REPLIED = "replied"
    FORWARDED = "forwarded"


class ChannelEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:\-]{8,128}$")
    channel_type: Literal["discord", "openwebui", "api"] = "discord"
    channel_config_id: str | None = Field(default=None, max_length=128)
    channel_id_hash: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{16,64}$")
    author_id_hash: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{16,64}$")
    message_id: str | None = Field(default=None, max_length=128)
    request_preview: str | None = Field(default=None, max_length=1000)
    route: str | None = Field(default=None, max_length=128)
    required_capability: str | None = Field(default=None, max_length=128)
    forwarded_to_yggdrasil: bool = False
    status: ChannelEventStatus
    blocked_reason: str | None = Field(default=None, max_length=128)
    reply_preview: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=500)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    categories: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)
    refresh: bool = False
    fetch: bool = True
    max_age_seconds: int = Field(default=3600, ge=60, le=86400)

    @field_validator("source_ids", "categories")
    @classmethod
    def list_values_must_be_slug_like(cls, value: list[str]) -> list[str]:
        clean: list[str] = []
        for item in value:
            item = str(item).strip()
            if not SLUG_RE.match(item):
                raise ValueError("source_ids and categories must be slug-like")
            if item not in clean:
                clean.append(item)
        return clean
