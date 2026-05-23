from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CapabilityGapModel, CapabilityProposalModel
from app.schemas import ApprovalLevel
from app.services.capability_gateway import CapabilityError, get_capability
from app.services.validation_service import find_secret_paths, redact_secrets


class CapabilityGapError(ValueError):
    pass


class CapabilityGapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.v[0-9]+)?$", max_length=128)
    enabled: bool = True
    status: Literal["active", "disabled", "implemented", "superseded"] = "active"
    source: str = Field(default="config", max_length=64)
    route: Literal["propose_new_capability"] = "propose_new_capability"
    title: str = Field(min_length=3, max_length=255)
    purpose: str = Field(min_length=10, max_length=2000)
    suggested_capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.v[0-9]+)$", max_length=128)
    suggested_task_type: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$", max_length=128)
    likely_approval_level: ApprovalLevel = ApprovalLevel.L1_NOTIFY_ONLY
    trigger_terms: list[str] = Field(default_factory=list, min_length=1, max_length=40)
    context_terms: list[str] = Field(default_factory=list, max_length=60)
    exclude_terms: list[str] = Field(default_factory=list, max_length=40)
    required_inputs: list[str] = Field(default_factory=list, max_length=30)
    safety_rules: list[str] = Field(default_factory=list, max_length=30)
    non_goals: list[str] = Field(default_factory=list, max_length=30)
    review_notes: str = Field(default="", max_length=2000)
    linked_capability_proposal_id: str | None = Field(default=None, max_length=64)

    @field_validator(
        "trigger_terms",
        "context_terms",
        "exclude_terms",
        "required_inputs",
        "safety_rules",
        "non_goals",
        mode="before",
    )
    @classmethod
    def clean_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("must be a list")
        output: list[str] = []
        for item in value:
            cleaned = re.sub(r"\s+", " ", str(item or "")).strip()
            if not cleaned:
                continue
            if len(cleaned) > 160:
                raise ValueError("items must be 160 characters or shorter")
            if cleaned.lower() not in {existing.lower() for existing in output}:
                output.append(cleaned)
        return output

    @model_validator(mode="after")
    def validate_safe_gap(self) -> "CapabilityGapConfig":
        data = self.model_dump(mode="json")
        if find_secret_paths(data):
            raise ValueError("capability gap contains secret-like values")
        searchable = searchable_text(data)
        forbidden = (
            "approval nonce",
            "admin api key",
            "api key",
            "password",
            "private key",
            "webhook url",
        )
        for term in forbidden:
            if term in searchable:
                raise ValueError(f"capability gap contains forbidden term: {term}")
        if self.status in {"implemented", "superseded"} and self.enabled:
            raise ValueError("implemented or superseded gaps must not be enabled")
        return self


class CapabilityGapRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    gaps: list[CapabilityGapConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registry(self) -> "CapabilityGapRegistry":
        if self.version != 1:
            raise ValueError("capability_gaps.yaml version must be 1")
        ids = [gap.id for gap in self.gaps]
        if len(ids) != len(set(ids)):
            raise ValueError("capability gap ids must be unique")
        return self


def config_root() -> Path:
    policy_file = Path(get_settings().policy_file)
    if not policy_file.is_absolute():
        policy_file = Path.cwd() / policy_file
    return policy_file.parent


def capability_gap_registry_path() -> Path:
    return config_root() / "capability_gaps.yaml"


def load_capability_gap_registry(path: str | Path | None = None) -> CapabilityGapRegistry:
    registry_path = Path(path) if path else capability_gap_registry_path()
    if not registry_path.exists():
        return CapabilityGapRegistry()
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return CapabilityGapRegistry.model_validate(data)


def validate_capability_gap_registry(path: str | Path | None = None) -> None:
    load_capability_gap_registry(path)


def ensure_capability_gap_defaults(session: Session) -> None:
    registry = load_capability_gap_registry()
    for gap in registry.gaps:
        if session.get(CapabilityGapModel, gap.id):
            continue
        model = CapabilityGapModel(**gap_to_model_payload(gap, source="default_config"))
        session.add(model)
    session.commit()


def list_capability_gaps(session: Session, *, include_inactive: bool = True) -> list[CapabilityGapModel]:
    query = session.query(CapabilityGapModel)
    if not include_inactive:
        query = query.filter(CapabilityGapModel.enabled.is_(True), CapabilityGapModel.status == "active")
    return query.order_by(CapabilityGapModel.id.asc()).all()


def get_capability_gap(session: Session, gap_id: str) -> CapabilityGapModel:
    gap = session.get(CapabilityGapModel, gap_id)
    if not gap:
        raise CapabilityGapError("capability gap not found")
    return gap


def upsert_capability_gap(
    session: Session,
    payload: CapabilityGapConfig,
    *,
    source: str = "ops_dashboard",
) -> CapabilityGapModel:
    clean = CapabilityGapConfig.model_validate({**payload.model_dump(mode="json"), "source": source})
    gap = session.get(CapabilityGapModel, clean.id)
    if gap is None:
        gap = CapabilityGapModel(**gap_to_model_payload(clean, source=source))
        session.add(gap)
    else:
        apply_gap_payload(gap, clean, source=source)
    return gap


def upsert_capability_gap_from_proposal(session: Session, proposal: CapabilityProposalModel) -> CapabilityGapModel:
    payload = CapabilityGapConfig(
        id=proposal.suggested_capability_id,
        enabled=proposal.status not in {"implemented", "superseded", "rejected", "closed"},
        status="active" if proposal.status not in {"implemented", "superseded"} else proposal.status,
        source="capability_proposal",
        title=proposal.title,
        purpose=proposal.purpose,
        suggested_capability_id=proposal.suggested_capability_id,
        suggested_task_type=proposal.suggested_task_type,
        likely_approval_level=ApprovalLevel(proposal.likely_approval_level),
        trigger_terms=proposal_trigger_terms(proposal),
        context_terms=proposal_context_terms(proposal),
        required_inputs=[str(item) for item in proposal.required_inputs],
        safety_rules=[str(item) for item in proposal.safety_rules],
        non_goals=[str(item) for item in proposal.non_goals],
        review_notes=proposal.review_notes or "Generated from capability proposal.",
        linked_capability_proposal_id=proposal.id,
    )
    existing = session.get(CapabilityGapModel, payload.id)
    if existing is not None and existing.source == "ops_dashboard":
        existing.linked_capability_proposal_id = proposal.id
        return existing
    return upsert_capability_gap(session, payload, source="capability_proposal")


def sync_capability_gap_status(
    session: Session,
    capability_id: str,
    *,
    status: str,
    proposal_id: str | None = None,
) -> CapabilityGapModel | None:
    gap = session.get(CapabilityGapModel, capability_id)
    if gap is None:
        return None
    if proposal_id:
        gap.linked_capability_proposal_id = proposal_id
    if status in {"implemented", "superseded"}:
        gap.status = status
        gap.enabled = False
    elif status in {"rejected", "closed"}:
        gap.status = "disabled"
        gap.enabled = False
    elif status in {"accepted", "implementation_planned", "pending"}:
        gap.status = "active"
    return gap


def capability_gap_to_dict(gap: CapabilityGapModel, *, include_match_state: bool = True) -> dict[str, Any]:
    registered = False
    try:
        get_capability(gap.suggested_capability_id)
    except CapabilityError:
        registered = False
    else:
        registered = True
    effective_enabled = bool(gap.enabled and gap.status == "active" and not registered)
    payload = {
        "id": gap.id,
        "enabled": gap.enabled,
        "effective_enabled": effective_enabled,
        "status": gap.status,
        "source": gap.source,
        "route": gap.route,
        "title": gap.title,
        "purpose": gap.purpose,
        "suggested_capability_id": gap.suggested_capability_id,
        "suggested_task_type": gap.suggested_task_type,
        "likely_approval_level": gap.likely_approval_level,
        "trigger_terms": list(gap.trigger_terms or []),
        "context_terms": list(gap.context_terms or []),
        "exclude_terms": list(gap.exclude_terms or []),
        "required_inputs": list(gap.required_inputs or []),
        "safety_rules": list(gap.safety_rules or []),
        "non_goals": list(gap.non_goals or []),
        "review_notes": gap.review_notes,
        "linked_capability_proposal_id": gap.linked_capability_proposal_id,
        "registered_capability_exists": registered,
        "created_at": gap.created_at,
        "updated_at": gap.updated_at,
    }
    if not include_match_state:
        payload.pop("effective_enabled", None)
        payload.pop("registered_capability_exists", None)
    return redact_secrets(payload)


def match_capability_gap(text: str, gaps: list[dict[str, Any]]) -> dict[str, Any] | None:
    lowered = normalize_search_text(text)
    if not lowered:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for gap in gaps:
        if not gap.get("effective_enabled", gap.get("enabled", False)):
            continue
        if str(gap.get("status") or "active") != "active":
            continue
        if str(gap.get("route") or "") != "propose_new_capability":
            continue
        trigger_terms = [str(item) for item in gap.get("trigger_terms", []) if str(item).strip()]
        context_terms = [str(item) for item in gap.get("context_terms", []) if str(item).strip()]
        exclude_terms = [str(item) for item in gap.get("exclude_terms", []) if str(item).strip()]
        if any(term_matches(lowered, term) for term in exclude_terms):
            continue
        trigger_score = sum(1 for term in trigger_terms if term_matches(lowered, term))
        if trigger_score == 0:
            continue
        context_score = sum(1 for term in context_terms if term_matches(lowered, term))
        if context_terms and context_score == 0:
            continue
        score = trigger_score * 3 + context_score
        if best is None or score > best[0]:
            best = (score, gap)
    return best[1] if best else None


def gap_to_model_payload(gap: CapabilityGapConfig, *, source: str) -> dict[str, Any]:
    data = gap.model_dump(mode="json")
    data["source"] = source
    data["likely_approval_level"] = gap.likely_approval_level.value
    return redact_secrets(data)


def apply_gap_payload(gap: CapabilityGapModel, payload: CapabilityGapConfig, *, source: str) -> None:
    data = gap_to_model_payload(payload, source=source)
    for key, value in data.items():
        setattr(gap, key, value)


def proposal_trigger_terms(proposal: CapabilityProposalModel) -> list[str]:
    terms = []
    for value in [
        proposal.suggested_task_type,
        proposal.suggested_capability_id.replace(".v1", "").replace("_", " "),
        proposal.title,
    ]:
        terms.extend(extract_terms(value))
    return terms[:12] or [proposal.suggested_task_type]


def proposal_context_terms(proposal: CapabilityProposalModel) -> list[str]:
    text = searchable_text(
        {
            "purpose": proposal.purpose,
            "inputs": proposal.required_inputs,
            "safety": proposal.safety_rules,
            "request": proposal.original_request_preview,
        }
    )
    context = []
    for term in ("monitor", "check", "alert", "notify", "status", "schedule", "automation", "endpoint", "threshold"):
        if term in text:
            context.append(term)
    return context or ["automation"]


def extract_terms(value: str) -> list[str]:
    normalized = normalize_search_text(value)
    words = [word for word in normalized.split() if len(word) >= 3 and word not in {"and", "the", "for", "with"}]
    terms = []
    if normalized and len(normalized) <= 60:
        terms.append(normalized)
    terms.extend(words[:8])
    return dedupe(terms)


def normalize_search_text(value: str) -> str:
    text = str(value or "").lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9äöüß.% ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def term_matches(haystack: str, term: str) -> bool:
    normalized = normalize_search_text(term)
    if not normalized:
        return False
    if " " in normalized:
        return normalized in haystack
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", haystack))


def searchable_text(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=True, allow_unicode=False).lower()


def dedupe(items: list[str]) -> list[str]:
    output: list[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned.lower() not in {existing.lower() for existing in output}:
            output.append(cleaned)
    return output
