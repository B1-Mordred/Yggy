from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from registry_lib import ROOT, RegistryError, ensure_import_paths, load_yaml_file, validate_task_against_policy

ensure_import_paths()

from app.schemas import ApprovalLevel, N8nWebhookRegistryConfig, PrinterRegistryConfig, SourceRegistryConfig  # noqa: E402


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{2,127}$")


class TemplateError(RegistryError):
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
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "task_type": self.task_type,
            "default_approval_level": self.default_approval_level.value,
            "allowed_output_targets": self.allowed_output_targets,
            "required_fields": self.required_fields,
            "optional_fields": self.optional_fields,
            "safety_notes": self.safety_notes,
            "example_prompts": self.example_prompts,
        }


def templates_directory(root: Path = ROOT) -> Path:
    return root / "configs" / "task_templates"


def load_templates(templates_dir: Path | None = None) -> dict[str, TaskTemplate]:
    directory = templates_dir or templates_directory()
    templates: dict[str, TaskTemplate] = {}
    if not directory.exists():
        return templates
    for path in sorted(directory.glob("*.yaml")):
        template = TaskTemplate.model_validate(load_yaml_file(path))
        if template.id in templates:
            raise TemplateError(f"duplicate task template id: {template.id}")
        templates[template.id] = template
    return templates


def get_template(template_id: str, templates_dir: Path | None = None) -> TaskTemplate:
    templates = load_templates(templates_dir)
    try:
        return templates[template_id]
    except KeyError as exc:
        available = ", ".join(sorted(templates)) or "none"
        raise TemplateError(f"unknown task template `{template_id}`; available templates: {available}") from exc


def render_task_from_template(
    template_id: str,
    values: dict[str, Any],
    *,
    templates_dir: Path | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    template = get_template(template_id, templates_dir)
    config_root = config_dir or ROOT / "configs"
    render_values = dict(values)
    require_render_values(template, render_values)

    task = copy.deepcopy(template.defaults)
    task["id"] = clean_required_string(render_values["id"], "id")
    task["name"] = clean_required_string(render_values["name"], "name")
    task["type"] = template.task_type
    task["enabled"] = False
    task["owner"] = clean_optional_string(render_values.get("owner"), task.get("owner", "local_user"))
    task["created_by"] = clean_optional_string(render_values.get("created_by"), task.get("created_by", "yggdrasil"))

    apply_trigger_overrides(task, render_values)
    apply_output_overrides(task, template, render_values)
    apply_policy_overrides(task, template, render_values)
    apply_runtime_overrides(task, render_values)

    if template.task_type == "topic_digest":
        apply_topic_digest_fields(task, template, render_values, config_root)
    if template.task_type == "server_health":
        apply_server_health_fields(task, render_values, config_root)
    if template.task_type == "printer_supply_status":
        apply_printer_supply_fields(task, render_values, config_root)
    if template.task_type == "n8n_webhook":
        apply_n8n_webhook_fields(task, render_values, config_root)

    validated = validate_task_against_policy(task)
    return drop_none_values(validated)


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


def coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise TemplateError(f"{name} must be true or false")


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


def apply_runtime_overrides(task: dict[str, Any], values: dict[str, Any]) -> None:
    runtime = task.setdefault("runtime", {})
    if values.get("dry_run") is not None and coerce_bool(values["dry_run"], "dry_run") is not True:
        raise TemplateError("rendered template tasks must remain runtime.dry_run=true")
    runtime["dry_run"] = True


def apply_topic_digest_fields(
    task: dict[str, Any],
    template: TaskTemplate,
    values: dict[str, Any],
    config_dir: Path,
) -> None:
    source_ids = coerce_string_list(values.get("source_ids"), "source_ids") if values.get("source_ids") is not None else template.default_source_ids
    if not source_ids:
        raise TemplateError("topic_digest templates require at least one source_id")

    approved_sources = load_enabled_sources(config_dir)
    rendered_sources: list[dict[str, Any]] = []
    for source_id in source_ids:
        source = approved_sources.get(source_id)
        if source is None:
            raise TemplateError(f"source_id `{source_id}` is not enabled in approved_sources.yaml")
        rendered = {"source_id": source.id, "type": source.type}
        if source.url:
            rendered["url"] = source.url
        if source.query:
            rendered["query"] = source.query
        rendered_sources.append(rendered)
    task["sources"] = rendered_sources

    filters = task.setdefault("filters", {})
    if values.get("include") is not None:
        filters["include"] = coerce_string_list(values["include"], "include")
    if values.get("exclude") is not None:
        filters["exclude"] = coerce_string_list(values["exclude"], "exclude")


def apply_server_health_fields(task: dict[str, Any], values: dict[str, Any], config_dir: Path) -> None:
    if values.get("check_ids") is None:
        return
    check_ids = coerce_string_list(values["check_ids"], "check_ids")
    approved_checks = load_enabled_service_checks(config_dir)
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


def apply_printer_supply_fields(task: dict[str, Any], values: dict[str, Any], config_dir: Path) -> None:
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
    approved = load_enabled_printers(config_dir)
    supplies: list[dict[str, Any]] = []
    for printer_id in printer_ids:
        printer = approved.get(printer_id)
        if printer is None:
            raise TemplateError(f"printer_id `{printer_id}` is not enabled in printers.yaml")
        supplies.append(printer.to_task_endpoint(low_threshold_percent=threshold).model_dump(mode="json"))
    task["printer_supplies"] = supplies


def apply_n8n_webhook_fields(task: dict[str, Any], values: dict[str, Any], config_dir: Path) -> None:
    n8n = task.setdefault("n8n", {})
    webhook_id = str(values.get("webhook_id") or n8n.get("webhook_id") or "").strip()
    if not webhook_id:
        raise TemplateError("n8n_webhook templates require webhook_id")
    approved = load_enabled_n8n_webhooks(config_dir)
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


def load_enabled_sources(config_dir: Path) -> dict[str, Any]:
    registry_path = config_dir / "sources" / "approved_sources.yaml"
    registry = SourceRegistryConfig.model_validate(load_yaml_file(registry_path))
    return {source.id: source for source in registry.sources if source.enabled}


def load_enabled_service_checks(config_dir: Path) -> dict[str, dict[str, Any]]:
    registry_path = config_dir / "metrics" / "services.yaml"
    data = load_yaml_file(registry_path)
    services = data.get("services") if isinstance(data, dict) else []
    enabled: dict[str, dict[str, Any]] = {}
    for service in services if isinstance(services, list) else []:
        if not isinstance(service, dict) or service.get("enabled") is False:
            continue
        service_id = str(service.get("id") or "").strip()
        if service_id:
            enabled[service_id] = service
    return enabled


def load_enabled_printers(config_dir: Path) -> dict[str, Any]:
    registry_path = config_dir / "printers" / "printers.yaml"
    registry = PrinterRegistryConfig.model_validate(load_yaml_file(registry_path))
    return {printer.id: printer for printer in registry.printers if printer.enabled}


def load_enabled_n8n_webhooks(config_dir: Path) -> dict[str, Any]:
    registry_path = config_dir / "n8n" / "webhooks.yaml"
    registry = N8nWebhookRegistryConfig.model_validate(load_yaml_file(registry_path))
    return {webhook.id: webhook for webhook in registry.webhooks if webhook.enabled}


def drop_none_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: drop_none_values(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [drop_none_values(item) for item in value]
    return value
