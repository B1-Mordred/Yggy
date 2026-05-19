from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import get_settings
from app.policy import load_n8n_webhook_registry, load_policy, load_printer_registry, load_source_registry, validate_task_policy
from app.schemas import ApprovalLevel, SourceConfig, TaskConfig, TaskTemplateRenderRequest, TaskTemplateSummary
from app.services.validation_service import find_secret_paths


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{2,127}$")


class TemplateError(ValueError):
    pass


class UnknownTemplateError(TemplateError):
    pass


class TaskTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    task_type: str
    default_approval_level: ApprovalLevel
    allowed_output_targets: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)
    default_source_ids: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    example_prompts: list[str] = Field(default_factory=list)
    defaults: dict[str, Any]

    @field_validator("id", "task_type")
    @classmethod
    def id_must_be_slug_like(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("template id and task_type must be slug-like")
        return value

    @field_validator("allowed_output_targets", "required_fields", "optional_fields", "default_source_ids")
    @classmethod
    def lists_must_be_plain_strings(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if len(cleaned) != len(value):
            raise ValueError("template string lists may not contain empty values")
        return cleaned

    @model_validator(mode="after")
    def validate_defaults(self) -> "TaskTemplate":
        output = self.defaults.get("output") if isinstance(self.defaults, dict) else None
        policy = self.defaults.get("policy") if isinstance(self.defaults, dict) else None
        runtime = self.defaults.get("runtime") if isinstance(self.defaults, dict) else None
        if not isinstance(output, dict):
            raise ValueError("template defaults.output is required")
        if not isinstance(policy, dict):
            raise ValueError("template defaults.policy is required")
        if not isinstance(runtime, dict):
            raise ValueError("template defaults.runtime is required")
        if not self.allowed_output_targets:
            raise ValueError("allowed_output_targets may not be empty")
        if output.get("target") not in self.allowed_output_targets:
            raise ValueError("defaults.output.target must be in allowed_output_targets")
        if policy.get("approval_level") != self.default_approval_level.value:
            raise ValueError("defaults.policy.approval_level must match default_approval_level")
        for forbidden in ("allow_shell", "allow_docker_socket"):
            if policy.get(forbidden) is True:
                raise ValueError(f"{forbidden}=true is forbidden in task templates")
        if self.defaults.get("enabled") is True:
            raise ValueError("template defaults may not enable rendered tasks")
        if runtime.get("dry_run") is not True:
            raise ValueError("template defaults.runtime.dry_run must be true")
        if find_secret_paths(self.model_dump(mode="json")):
            raise ValueError("template contains plain-text secret-like values")
        return self

    def summary(self) -> TaskTemplateSummary:
        return TaskTemplateSummary(
            id=self.id,
            name=self.name,
            description=self.description,
            task_type=self.task_type,
            default_approval_level=self.default_approval_level,
            allowed_output_targets=self.allowed_output_targets,
            required_fields=self.required_fields,
            optional_fields=self.optional_fields,
            safety_notes=self.safety_notes,
            example_prompts=self.example_prompts,
        )


def config_root() -> Path:
    policy_file = Path(get_settings().policy_file)
    if not policy_file.is_absolute():
        policy_file = Path.cwd() / policy_file
    return policy_file.parent


def templates_directory() -> Path:
    return config_root() / "task_templates"


def load_yaml_file(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TemplateError(f"{path} did not contain a YAML mapping")
    return data


def load_templates() -> dict[str, TaskTemplate]:
    directory = templates_directory()
    templates: dict[str, TaskTemplate] = {}
    if not directory.exists():
        return templates
    for path in sorted(directory.glob("*.yaml")):
        template = TaskTemplate.model_validate(load_yaml_file(path))
        if template.id in templates:
            raise TemplateError(f"duplicate task template id: {template.id}")
        templates[template.id] = template
    return templates


def get_template(template_id: str) -> TaskTemplate:
    templates = load_templates()
    try:
        return templates[template_id]
    except KeyError as exc:
        raise UnknownTemplateError(f"unknown task template: {template_id}") from exc


def render_task_from_template(template_id: str, request: TaskTemplateRenderRequest) -> TaskConfig:
    template = get_template(template_id)
    values = request.model_dump(mode="json", exclude_none=True)
    require_render_values(template, values)

    task = copy.deepcopy(template.defaults)
    task["id"] = clean_required_string(values["id"], "id")
    task["name"] = clean_required_string(values["name"], "name")
    task["type"] = template.task_type
    task["enabled"] = False
    task["owner"] = clean_optional_string(values.get("owner"), task.get("owner", "local_user"))
    task["created_by"] = clean_optional_string(values.get("created_by"), task.get("created_by", "yggdrasil"))

    apply_trigger_overrides(task, values)
    apply_output_overrides(task, template, values)
    apply_policy_overrides(task, template, values)
    apply_runtime_overrides(task)

    if template.task_type == "topic_digest":
        apply_topic_digest_fields(task, template, values)
    if template.task_type == "server_health":
        apply_server_health_fields(task, values)
    if template.task_type == "printer_supply_status":
        apply_printer_supply_fields(task, values)
    if template.task_type == "n8n_webhook":
        apply_n8n_webhook_fields(task, values)

    task_config = TaskConfig.model_validate(task)
    validate_task_policy(task_config, load_policy())
    return task_config


def require_render_values(template: TaskTemplate, values: dict[str, Any]) -> None:
    missing = [
        field_name
        for field_name in template.required_fields
        if field_name not in values or values[field_name] in (None, "", [])
    ]
    if missing:
        raise TemplateError(f"required template values missing: {', '.join(missing)}")


def clean_required_string(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise TemplateError(f"{name} is required")
    return text


def clean_optional_string(value: Any, default: str) -> str:
    if value is None:
        return str(default)
    text = str(value).strip()
    return text or str(default)


def coerce_string_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raise TemplateError(f"{name} must be a string or list of strings")
    items = [str(item).strip() for item in raw_items if str(item).strip()]
    if not items:
        raise TemplateError(f"{name} may not be empty")
    return items


def apply_trigger_overrides(task: dict[str, Any], values: dict[str, Any]) -> None:
    trigger = task.setdefault("trigger", {})
    if values.get("cron"):
        trigger["cron"] = str(values["cron"]).strip()
    if values.get("timezone"):
        trigger["timezone"] = str(values["timezone"]).strip()


def apply_output_overrides(task: dict[str, Any], template: TaskTemplate, values: dict[str, Any]) -> None:
    output = task.setdefault("output", {})
    target = str(values.get("output_target") or output.get("target") or "").strip()
    if target not in template.allowed_output_targets:
        allowed = ", ".join(template.allowed_output_targets)
        raise TemplateError(f"output target `{target}` is not allowed for template `{template.id}`; allowed: {allowed}")
    output["target"] = target


def apply_policy_overrides(task: dict[str, Any], template: TaskTemplate, values: dict[str, Any]) -> None:
    policy = task.setdefault("policy", {})
    policy["approval_level"] = template.default_approval_level.value
    if values.get("max_items") is not None:
        try:
            policy["max_items"] = int(values["max_items"])
        except Exception as exc:
            raise TemplateError("max_items must be an integer") from exc
    policy["allow_external_side_effects"] = False
    policy["allow_shell"] = False
    policy["allow_docker_socket"] = False
    policy["allow_filesystem_write"] = False


def apply_runtime_overrides(task: dict[str, Any]) -> None:
    runtime = task.setdefault("runtime", {})
    runtime["dry_run"] = True


def apply_topic_digest_fields(task: dict[str, Any], template: TaskTemplate, values: dict[str, Any]) -> None:
    source_ids = coerce_string_list(values.get("source_ids"), "source_ids") if values.get("source_ids") is not None else template.default_source_ids
    if not source_ids:
        raise TemplateError("topic_digest templates require at least one source_id")

    approved_sources = {source.id: source for source in load_source_registry(load_policy()).sources if source.enabled}
    rendered_sources: list[dict[str, Any]] = []
    for source_id in source_ids:
        source = approved_sources.get(source_id)
        if source is None:
            raise TemplateError(f"source_id `{source_id}` is not enabled in approved_sources.yaml")
        rendered = SourceConfig(source_id=source.id, type=source.type, url=source.url, query=source.query).model_dump(
            mode="json",
            exclude_none=True,
        )
        rendered_sources.append(rendered)
    task["sources"] = rendered_sources

    filters = task.setdefault("filters", {})
    if values.get("include") is not None:
        filters["include"] = coerce_string_list(values["include"], "include")
    if values.get("exclude") is not None:
        filters["exclude"] = coerce_string_list(values["exclude"], "exclude")


def apply_server_health_fields(task: dict[str, Any], values: dict[str, Any]) -> None:
    if values.get("check_ids") is None:
        return
    check_ids = coerce_string_list(values["check_ids"], "check_ids")
    approved_checks = load_enabled_service_checks()
    checks: list[dict[str, Any]] = []
    for check_id in check_ids:
        service = approved_checks.get(check_id)
        if service is None:
            raise TemplateError(f"check_id `{check_id}` is not enabled in metrics/services.yaml")
        check = {
            "type": service.get("type", "http_health"),
            "name": service["id"],
            "url": service["url"],
        }
        if service.get("expected_status") is not None:
            check["expected_status"] = int(service["expected_status"])
        if service.get("type") == "worker_heartbeat":
            check["max_age_seconds"] = int(service.get("max_age_seconds") or 180)
        checks.append(check)
    task["checks"] = checks


def apply_printer_supply_fields(task: dict[str, Any], values: dict[str, Any]) -> None:
    if values.get("printer_ids") is None:
        return
    printer_ids = coerce_string_list(values["printer_ids"], "printer_ids")
    threshold = values.get("low_threshold_percent")
    if threshold is not None:
        try:
            threshold = int(threshold)
        except Exception as exc:
            raise TemplateError("low_threshold_percent must be an integer") from exc
        if threshold < 1 or threshold > 100:
            raise TemplateError("low_threshold_percent must be between 1 and 100")
    approved_printers = {printer.id: printer for printer in load_printer_registry(load_policy()).printers if printer.enabled}
    supplies: list[dict[str, Any]] = []
    for printer_id in printer_ids:
        printer = approved_printers.get(printer_id)
        if printer is None:
            raise TemplateError(f"printer_id `{printer_id}` is not enabled in printers.yaml")
        supplies.append(printer.to_task_endpoint(low_threshold_percent=threshold).model_dump(mode="json"))
    task["printer_supplies"] = supplies


def apply_n8n_webhook_fields(task: dict[str, Any], values: dict[str, Any]) -> None:
    n8n = task.setdefault("n8n", {})
    webhook_id = str(values.get("webhook_id") or n8n.get("webhook_id") or "").strip()
    if not webhook_id:
        raise TemplateError("n8n_webhook templates require webhook_id")
    approved = {webhook.id: webhook for webhook in load_n8n_webhook_registry(load_policy()).webhooks if webhook.enabled}
    webhook = approved.get(webhook_id)
    if webhook is None:
        raise TemplateError(f"webhook_id `{webhook_id}` is not enabled in n8n/webhooks.yaml")
    n8n["webhook_id"] = webhook.id
    n8n["path"] = webhook.path
    n8n["method"] = webhook.method
    if values.get("n8n_payload") is not None:
        payload = values["n8n_payload"]
        if not isinstance(payload, dict):
            raise TemplateError("n8n_payload must be an object")
        n8n["payload"] = payload


def load_enabled_service_checks() -> dict[str, dict[str, Any]]:
    metrics_path = config_root() / "metrics" / "services.yaml"
    if not metrics_path.exists():
        return {}
    data = yaml.safe_load(metrics_path.read_text(encoding="utf-8")) or {}
    services = data.get("services") if isinstance(data, dict) else []
    enabled: dict[str, dict[str, Any]] = {}
    for service in services if isinstance(services, list) else []:
        if not isinstance(service, dict) or service.get("enabled") is False:
            continue
        service_id = str(service.get("id") or "").strip()
        if service_id:
            enabled[service_id] = service
    return enabled
