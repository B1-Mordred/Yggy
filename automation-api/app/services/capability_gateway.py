from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import get_settings
from app.policy import load_n8n_webhook_registry, load_policy, load_source_registry
from app.schemas import ApprovalLevel, CanonicalIntent, CapabilityGatewayResult, GatewayOutcome, TaskTemplateRenderRequest
from app.services.task_template_service import get_template
from app.services.validation_service import find_secret_paths


SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,127}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{2,127}$")
GLOBAL_UNSAFE_KEYWORDS = {
    "allow_shell",
    "allow_docker_socket",
    "docker socket",
    "docker exec",
    "restart docker",
    "shell command",
    "delete files",
    "reorganize files",
    "firewall",
    "iptables",
    "ufw",
    "password",
    "api key",
    "token",
    "private key",
    "webhook url",
    "purchase",
    "buy ",
}


class CapabilityError(ValueError):
    pass


class CapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    purpose: str
    maps_to_task_type: str
    maps_to_template: str
    deterministic_action: str = "draft_task_from_template"
    allowed_approval_levels: list[ApprovalLevel]
    default_approval_level: ApprovalLevel
    allowed_output_targets: list[str] = Field(default_factory=list)
    required_slots: list[str] = Field(default_factory=list)
    optional_slots: list[str] = Field(default_factory=list)
    allowed_source_ids: list[str] = Field(default_factory=list)
    allow_any_approved_source: bool = False
    allowed_check_ids: list[str] = Field(default_factory=list)
    allowed_webhook_ids: list[str] = Field(default_factory=list)
    safety_rules: list[str] = Field(default_factory=list)
    unsafe_keywords: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def id_must_be_versioned(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.v[0-9]+$", value):
            raise ValueError("capability id must look like name.v1")
        return value

    @field_validator("maps_to_task_type", "maps_to_template")
    @classmethod
    def task_type_must_be_slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("task type/template must be slug-like")
        return value

    @field_validator(
        "allowed_output_targets",
        "required_slots",
        "optional_slots",
        "allowed_source_ids",
        "allowed_check_ids",
        "allowed_webhook_ids",
    )
    @classmethod
    def list_items_must_be_plain(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if len(cleaned) != len(value):
            raise ValueError("capability list entries may not be empty")
        return cleaned

    @model_validator(mode="after")
    def validate_definition(self) -> "CapabilityDefinition":
        if self.default_approval_level not in self.allowed_approval_levels:
            raise ValueError("default_approval_level must be allowed")
        if self.deterministic_action not in {"draft_task_from_template", "propose_task_change"}:
            raise ValueError("unsupported deterministic_action in capability registry v1")
        return self

    def summary(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"unsafe_keywords"})


class CapabilityRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    capabilities: list[CapabilityDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registry(self) -> "CapabilityRegistry":
        if self.version != 1:
            raise ValueError("capabilities.yaml version must be 1")
        ids = [capability.id for capability in self.capabilities]
        if len(ids) != len(set(ids)):
            raise ValueError("capability ids must be unique")
        return self


def config_root() -> Path:
    policy_file = Path(get_settings().policy_file)
    if not policy_file.is_absolute():
        policy_file = Path.cwd() / policy_file
    return policy_file.parent


def capability_registry_path() -> Path:
    return config_root() / "capabilities.yaml"


def load_capability_registry(path: str | Path | None = None) -> CapabilityRegistry:
    registry_path = Path(path) if path else capability_registry_path()
    if not registry_path.exists():
        return CapabilityRegistry()
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return CapabilityRegistry.model_validate(data)


def get_capability(capability_id: str) -> CapabilityDefinition:
    for capability in load_capability_registry().capabilities:
        if capability.id == capability_id:
            return capability
    raise CapabilityError(f"unknown capability: {capability_id}")


def validate_capability_registry(path: str | Path | None = None) -> None:
    registry = load_capability_registry(path)
    policy = load_policy()
    source_ids = {source.id for source in load_source_registry(policy).sources if source.enabled}
    webhook_ids = {webhook.id for webhook in load_n8n_webhook_registry(policy).webhooks if webhook.enabled}
    errors: list[str] = []
    for capability in registry.capabilities:
        try:
            template = get_template(capability.maps_to_template)
        except Exception as exc:
            errors.append(f"{capability.id}: mapped template is invalid: {exc}")
            continue
        if template.task_type != capability.maps_to_task_type:
            errors.append(f"{capability.id}: maps_to_task_type does not match template task_type")
        for target in capability.allowed_output_targets:
            if target not in template.allowed_output_targets:
                errors.append(f"{capability.id}: output target {target} is not allowed by template")
        for source_id in capability.allowed_source_ids:
            if source_id not in source_ids:
                errors.append(f"{capability.id}: source_id {source_id} is not enabled in approved source registry")
        for webhook_id in capability.allowed_webhook_ids:
            if webhook_id not in webhook_ids:
                errors.append(f"{capability.id}: webhook_id {webhook_id} is not enabled in approved n8n registry")
    if errors:
        raise CapabilityError("; ".join(errors))


def validate_intent(intent: CanonicalIntent, *, prepare: bool = False) -> CapabilityGatewayResult:
    try:
        capability = get_capability(intent.capability_id)
    except CapabilityError as exc:
        return CapabilityGatewayResult(
            outcome=GatewayOutcome.REJECT_UNSUPPORTED,
            capability_id=intent.capability_id,
            message=str(exc),
        )

    unsupported = new_capability_reason(intent)
    if unsupported:
        return CapabilityGatewayResult(
            outcome=GatewayOutcome.PROPOSE_NEW_CAPABILITY,
            capability_id=capability.id,
            message=unsupported,
        )

    unsafe_reasons = unsafe_intent_reasons(intent, capability)
    if unsafe_reasons:
        return CapabilityGatewayResult(
            outcome=GatewayOutcome.REJECT_UNSAFE,
            capability_id=capability.id,
            message="The request contains unsafe or forbidden automation material.",
            unsafe_reasons=unsafe_reasons,
            confirmation_summary=confirmation_summary(intent, capability),
        )

    missing = missing_slots(intent, capability)
    if missing:
        return CapabilityGatewayResult(
            outcome=GatewayOutcome.ASK_CLARIFICATION,
            capability_id=capability.id,
            message="Required capability information is missing.",
            missing_slots=missing,
            confirmation_summary=confirmation_summary(intent, capability),
        )

    slot_errors = validate_slots(intent, capability)
    if slot_errors:
        return CapabilityGatewayResult(
            outcome=GatewayOutcome.REJECT_UNSAFE,
            capability_id=capability.id,
            message="The canonical intent failed capability safety validation.",
            unsafe_reasons=slot_errors,
            confirmation_summary=confirmation_summary(intent, capability),
        )

    summary = confirmation_summary(intent, capability)
    if intent.requires_user_confirmation and not intent.user_confirmation_obtained:
        return CapabilityGatewayResult(
            outcome=GatewayOutcome.ASK_CLARIFICATION,
            capability_id=capability.id,
            message="User confirmation is required before forwarding to Yggdrasil.",
            missing_slots=["user_confirmation"],
            confirmation_summary=summary,
        )

    request = build_yggdrasil_request(intent, capability)
    return CapabilityGatewayResult(
        outcome=GatewayOutcome.ACCEPT,
        capability_id=capability.id,
        message="Canonical intent accepted.",
        confirmation_summary=summary,
        yggdrasil_request=request if prepare else request,
    )


def new_capability_reason(intent: CanonicalIntent) -> str | None:
    text = searchable_text(intent)
    if any(term in text for term in ("printer", "toner", "ink level", "cartridge")):
        return "This looks useful, but no printer or toner capability is registered yet."
    return None


def missing_slots(intent: CanonicalIntent, capability: CapabilityDefinition) -> list[str]:
    missing: list[str] = []
    for slot_name in capability.required_slots:
        value = intent.slots.get(slot_name)
        if value in (None, "", []):
            missing.append(slot_name)
    if capability.deterministic_action == "propose_task_change" and not has_topic_digest_subject_change(intent.slots):
        missing.append("subject_change")
    return missing


def unsafe_intent_reasons(intent: CanonicalIntent, capability: CapabilityDefinition) -> list[str]:
    reasons: list[str] = []
    text = searchable_text(intent)
    for keyword in sorted(GLOBAL_UNSAFE_KEYWORDS | {item.lower() for item in capability.unsafe_keywords}):
        if keyword and keyword in text:
            reasons.append(f"unsafe keyword or capability: {keyword}")
    if find_secret_paths({"slots": intent.slots, "user_request": intent.user_request or ""}):
        reasons.append("secret-like material appears in the intent")
    if truthy(intent.slots.get("allow_shell")):
        reasons.append("allow_shell is forbidden")
    if truthy(intent.slots.get("allow_docker_socket")):
        reasons.append("allow_docker_socket is forbidden")
    if intent.slots.get("webhook_url"):
        reasons.append("arbitrary webhook URLs are forbidden; use approved webhook_id")
    if capability.maps_to_task_type == "topic_digest" and (intent.slots.get("web_query") or intent.slots.get("query")):
        reasons.append(f"{capability.id} requires approved source IDs and filter terms, not web_query/query slots")
    if capability.maps_to_task_type == "topic_digest" and intent.slots.get("url"):
        reasons.append(f"{capability.id} does not accept arbitrary URLs; use approved source IDs")
    return reasons


def validate_slots(intent: CanonicalIntent, capability: CapabilityDefinition) -> list[str]:
    errors: list[str] = []
    slots = intent.slots
    task_id = str(slots.get("task_id") or "")
    if not SLUG_RE.match(task_id):
        errors.append("task_id must be slug-like")
    cron = str(slots.get("cron") or "")
    if cron and not croniter.is_valid(cron):
        errors.append("cron expression is invalid")
    timezone = str(slots.get("timezone") or "")
    if timezone:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            errors.append("timezone is invalid")
    output_target = str(slots.get("output_target") or "")
    if output_target and output_target not in capability.allowed_output_targets:
        errors.append(f"output target {output_target} is not allowed for {capability.id}")
    approval_level = str(slots.get("approval_level") or capability.default_approval_level.value)
    if approval_level not in {level.value for level in capability.allowed_approval_levels}:
        errors.append(f"approval level {approval_level} is not allowed for {capability.id}")
    errors.extend(validate_capability_specific_slots(slots, capability))
    if not errors and capability.deterministic_action == "draft_task_from_template":
        try:
            TaskTemplateRenderRequest.model_validate(template_values_from_slots(intent, capability))
        except Exception as exc:
            errors.append(f"template values are invalid: {exc}")
    return errors


def validate_capability_specific_slots(slots: dict[str, Any], capability: CapabilityDefinition) -> list[str]:
    errors: list[str] = []
    if capability.id == "topic_digest.v1":
        allowed_source_ids = source_ids_allowed_for_capability(capability)
        source_ids = list_slot(slots.get("source_ids"))
        for source_id in source_ids:
            if source_id not in allowed_source_ids:
                errors.append(f"source_id {source_id} is not allowed for {capability.id}")
    if capability.id == "topic_digest.modify_subjects.v1":
        allowed_source_ids = source_ids_allowed_for_capability(capability)
        for slot_name in ("add_source_ids", "remove_source_ids"):
            for source_id in list_slot(slots.get(slot_name)):
                if source_id not in allowed_source_ids:
                    errors.append(f"{slot_name} entry {source_id} is not allowed for {capability.id}")
        for slot_name in ("add_include", "remove_include"):
            for term in list_slot(slots.get(slot_name)):
                if len(term) > 120:
                    errors.append(f"{slot_name} entries must be 120 characters or shorter")
                if find_secret_paths({slot_name: term}):
                    errors.append(f"{slot_name} contains secret-like material")
    if capability.id == "server_health.v1":
        check_ids = list_slot(slots.get("check_ids"))
        for check_id in check_ids:
            if check_id not in capability.allowed_check_ids:
                errors.append(f"check_id {check_id} is not allowed for {capability.id}")
            if not SERVICE_ID_RE.match(check_id):
                errors.append(f"check_id {check_id} is not valid")
    if capability.id == "n8n_webhook.v1":
        webhook_id = str(slots.get("webhook_id") or "")
        if webhook_id not in capability.allowed_webhook_ids:
            errors.append(f"webhook_id {webhook_id} is not allowed for {capability.id}")
    return errors


def source_ids_allowed_for_capability(capability: CapabilityDefinition) -> set[str]:
    if capability.allow_any_approved_source:
        policy = load_policy()
        return {source.id for source in load_source_registry(policy).sources if source.enabled}
    return set(capability.allowed_source_ids)


def build_yggdrasil_request(intent: CanonicalIntent, capability: CapabilityDefinition) -> dict[str, Any]:
    if capability.deterministic_action == "propose_task_change":
        return {
            "action": capability.deterministic_action,
            "capability_id": capability.id,
            "task_id": intent.slots.get("task_id"),
            "change_type": "topic_digest_subjects",
            "change": {
                "add_source_ids": list_slot(intent.slots.get("add_source_ids")),
                "remove_source_ids": list_slot(intent.slots.get("remove_source_ids")),
                "add_include": list_slot(intent.slots.get("add_include")),
                "remove_include": list_slot(intent.slots.get("remove_include")),
                **({"output_target": str(intent.slots["output_target"])} if intent.slots.get("output_target") else {}),
            },
            "confirmation_summary": confirmation_summary(intent, capability),
        }
    return {
        "action": capability.deterministic_action,
        "capability_id": capability.id,
        "template_id": capability.maps_to_template,
        "template_values": template_values_from_slots(intent, capability),
        "confirmation_summary": confirmation_summary(intent, capability),
    }


def template_values_from_slots(intent: CanonicalIntent, capability: CapabilityDefinition) -> dict[str, Any]:
    slots = intent.slots
    values: dict[str, Any] = {
        "id": slots.get("task_id"),
        "name": slots.get("name"),
        "cron": slots.get("cron"),
        "timezone": slots.get("timezone"),
        "output_target": slots.get("output_target"),
        "owner": slots.get("owner") or "local_user",
        "created_by": "bragi",
    }
    if capability.id == "topic_digest.v1":
        values["source_ids"] = list_slot(slots.get("source_ids"))
        if slots.get("include") is not None:
            values["include"] = list_slot(slots.get("include"))
        if slots.get("exclude") is not None:
            values["exclude"] = list_slot(slots.get("exclude"))
        if slots.get("max_items") is not None:
            values["max_items"] = slots.get("max_items")
    if capability.id == "server_health.v1":
        values["check_ids"] = list_slot(slots.get("check_ids"))
    if capability.id == "n8n_webhook.v1":
        values["webhook_id"] = slots.get("webhook_id")
        if slots.get("payload_description"):
            values["n8n_payload"] = {"description": str(slots["payload_description"])[:500]}
    return {key: value for key, value in values.items() if value not in (None, "", [])}


def confirmation_summary(intent: CanonicalIntent, capability: CapabilityDefinition) -> dict[str, Any]:
    slots = intent.slots
    if capability.deterministic_action == "propose_task_change":
        return {
            "capability_id": capability.id,
            "purpose": capability.purpose,
            "task_id": slots.get("task_id"),
            "name": slots.get("name") or slots.get("task_id"),
            "change_type": "topic_digest_subjects",
            "add_source_ids": list_slot(slots.get("add_source_ids")),
            "remove_source_ids": list_slot(slots.get("remove_source_ids")),
            "add_include": list_slot(slots.get("add_include")),
            "remove_include": list_slot(slots.get("remove_include")),
            "output_target": slots.get("output_target"),
            "dry_run": None,
            "approval_level": str(slots.get("approval_level") or capability.default_approval_level.value),
            "worst_case_failure_mode": worst_case_failure_mode(capability),
            "rollback_disable_method": "Reject the pending task-change proposal, or pause/revert through the local /ops UI.",
            "safety_rules": capability.safety_rules,
        }
    schedule = {
        "cron": slots.get("cron"),
        "timezone": slots.get("timezone"),
    }
    return {
        "capability_id": capability.id,
        "purpose": capability.purpose,
        "task_id": slots.get("task_id"),
        "name": slots.get("name"),
        "schedule": schedule,
        "sources": list_slot(slots.get("source_ids")) if capability.id == "topic_digest.v1" else [],
        "checks": list_slot(slots.get("check_ids")) if capability.id == "server_health.v1" else [],
        "webhook_id": slots.get("webhook_id") if capability.id == "n8n_webhook.v1" else None,
        "output_target": slots.get("output_target"),
        "dry_run": True,
        "approval_level": str(slots.get("approval_level") or capability.default_approval_level.value),
        "worst_case_failure_mode": worst_case_failure_mode(capability),
        "rollback_disable_method": "Pause or reject the disabled draft through the local /ops UI or admin CLI.",
        "safety_rules": capability.safety_rules,
    }


def worst_case_failure_mode(capability: CapabilityDefinition) -> str:
    if capability.id == "server_health.v1":
        return "A noisy or incomplete alert could be sent to the whitelisted alerts target after approval."
    if capability.id == "topic_digest.v1":
        return "A noisy, incomplete, or incorrect digest could be sent to a whitelisted Discord target after approval."
    if capability.id == "topic_digest.modify_subjects.v1":
        return "The existing digest could become noisy, incomplete, or less relevant after the approved change is applied."
    if capability.id == "n8n_webhook.v1":
        return "An approved internal n8n workflow could receive an incorrect but bounded payload after approval."
    return "The task could produce incorrect output or fail within its configured policy."


def list_slot(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def has_topic_digest_subject_change(slots: dict[str, Any]) -> bool:
    for slot_name in ("add_source_ids", "remove_source_ids", "add_include", "remove_include"):
        if list_slot(slots.get(slot_name)):
            return True
    return bool(slots.get("output_target"))


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled", "allow"}
    return bool(value)


def searchable_text(intent: CanonicalIntent) -> str:
    return (json.dumps(intent.slots, sort_keys=True, default=str) + " " + (intent.user_request or "")).lower()
