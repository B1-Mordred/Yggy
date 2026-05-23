from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session, object_session

from app.models import CapabilityImplementationPlanModel, CapabilityProposalModel, utcnow
from app.schemas import CapabilityProposalCreate
from app.services.capability_gateway import CapabilityError, get_capability
from app.services.capability_gap_service import sync_capability_gap_status, upsert_capability_gap_from_proposal
from app.services.validation_service import find_secret_paths, redact_secrets


class CapabilityProposalError(ValueError):
    pass


IMPLEMENTATION_PLAN_STATUSES = {"implementation_planned", "implemented", "superseded"}
CAPABILITY_PROPOSAL_STATUSES = {
    "pending",
    "accepted",
    "rejected",
    "closed",
    *IMPLEMENTATION_PLAN_STATUSES,
}


FORBIDDEN_PROPOSAL_TERMS = {
    "arbitrary shell",
    "shell command",
    "docker socket",
    "docker exec",
    "privileged container",
    "host filesystem",
    "reorganize all files",
    "delete files",
    "firewall",
    "iptables",
    "ufw",
    "rotate credentials",
    "password",
    "api key",
    "token",
    "private key",
    "webhook url",
    "purchase",
    "buy ",
}


def create_capability_proposal(
    session: Session,
    payload: CapabilityProposalCreate,
) -> CapabilityProposalModel:
    validate_capability_proposal(payload)
    proposal = CapabilityProposalModel(
        id=str(uuid.uuid4()),
        status="pending",
        requested_by=payload.requested_by,
        source_channel=payload.source_channel,
        title=payload.title,
        original_request_preview=str(redact_secrets(payload.original_request_preview or ""))[:1000],
        purpose=str(redact_secrets(payload.purpose)),
        suggested_capability_id=payload.suggested_capability_id,
        suggested_task_type=payload.suggested_task_type,
        likely_approval_level=payload.likely_approval_level.value,
        required_inputs=redact_secrets(payload.required_inputs),
        safety_rules=redact_secrets(payload.safety_rules),
        non_goals=redact_secrets(payload.non_goals),
        review_notes=str(redact_secrets(payload.review_notes or "")),
    )
    session.add(proposal)
    upsert_capability_gap_from_proposal(session, proposal)
    return proposal


def validate_capability_proposal(payload: CapabilityProposalCreate) -> None:
    data = payload.model_dump(mode="json")
    errors: list[str] = []
    if find_secret_paths(data):
        errors.append("plain-text secret-like values found in capability proposal")
    try:
        get_capability(payload.suggested_capability_id)
    except CapabilityError:
        pass
    else:
        errors.append(f"capability is already registered: {payload.suggested_capability_id}")

    text = searchable_text(
        {
            "title": data.get("title"),
            "original_request_preview": data.get("original_request_preview"),
            "purpose": data.get("purpose"),
            "suggested_capability_id": data.get("suggested_capability_id"),
            "suggested_task_type": data.get("suggested_task_type"),
            "review_notes": data.get("review_notes"),
        }
    )
    for term in sorted(FORBIDDEN_PROPOSAL_TERMS):
        if term in text:
            errors.append(f"capability proposal contains forbidden unsafe term: {term}")
            break

    if payload.likely_approval_level.value == "L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE":
        errors.append("L4 capability proposals must be handled manually outside the model-facing proposal path")

    if errors:
        raise CapabilityProposalError("; ".join(errors))


def close_capability_proposal(proposal: CapabilityProposalModel, *, status: str, reason: str = "") -> None:
    if status not in {"accepted", "rejected", "closed"}:
        raise CapabilityProposalError("invalid capability proposal close status")
    allowed_current = {"pending"} if status == "accepted" else {"pending", "accepted"}
    if proposal.status not in allowed_current:
        raise CapabilityProposalError(f"capability proposal cannot move from {proposal.status} to {status}")
    proposal.status = status
    proposal.decided_at = utcnow()
    if reason:
        proposal.review_notes = append_review_note(proposal.review_notes, reason)
    # Keep Bragi's capability-gap routing aligned with operator decisions.
    # Accepted proposals remain active gaps until the capability is implemented;
    # rejected or closed proposals stop generating new executable-looking routes.
    try:
        session = object_session(proposal)
        if session is not None:
            sync_capability_gap_status(session, proposal.suggested_capability_id, status=status, proposal_id=proposal.id)
    except Exception:
        pass


def create_implementation_plan(
    session: Session,
    proposal: CapabilityProposalModel,
    *,
    created_by: str = "ops_dashboard",
    reason: str = "",
) -> CapabilityImplementationPlanModel:
    if proposal.status != "accepted":
        raise CapabilityProposalError("capability proposal must be accepted before implementation planning")
    existing = implementation_plan_for_proposal(session, proposal.id)
    if existing:
        raise CapabilityProposalError("capability proposal already has an implementation plan")

    plan = CapabilityImplementationPlanModel(
        id=str(uuid.uuid4()),
        proposal_id=proposal.id,
        capability_id=proposal.suggested_capability_id,
        status="implementation_planned",
        created_by=created_by,
        summary=implementation_plan_summary(proposal),
        files_to_change=implementation_plan_files(proposal),
        required_decisions=implementation_plan_decisions(proposal),
        security_boundaries=implementation_plan_boundaries(proposal),
        acceptance_tests=implementation_plan_tests(proposal),
        review_notes=str(redact_secrets(reason or "Implementation planning created from accepted capability proposal.")),
    )
    session.add(plan)
    proposal.status = "implementation_planned"
    proposal.decided_at = utcnow()
    proposal.review_notes = append_review_note(proposal.review_notes, reason or "Implementation plan created.")
    sync_capability_gap_status(
        session,
        proposal.suggested_capability_id,
        status="implementation_planned",
        proposal_id=proposal.id,
    )
    return plan


def mark_implementation_plan_status(
    session: Session,
    proposal: CapabilityProposalModel,
    *,
    status: str,
    reason: str = "",
) -> CapabilityImplementationPlanModel:
    if status not in {"implemented", "superseded"}:
        raise CapabilityProposalError("invalid implementation plan status")
    plan = implementation_plan_for_proposal(session, proposal.id)
    if not plan:
        raise CapabilityProposalError("capability proposal has no implementation plan")
    if plan.status != "implementation_planned" or proposal.status != "implementation_planned":
        raise CapabilityProposalError("capability implementation plan is not active")
    if status == "implemented":
        try:
            get_capability(proposal.suggested_capability_id)
        except CapabilityError as exc:
            raise CapabilityProposalError(
                "capability is not registered yet; implement registry, worker, tests, and docs before marking implemented"
            ) from exc

    plan.status = status
    plan.completed_at = utcnow()
    if reason:
        plan.review_notes = append_review_note(plan.review_notes, reason)
    proposal.status = status
    proposal.decided_at = utcnow()
    proposal.review_notes = append_review_note(proposal.review_notes, reason or f"Implementation plan marked {status}.")
    sync_capability_gap_status(session, proposal.suggested_capability_id, status=status, proposal_id=proposal.id)
    return plan


def implementation_plan_for_proposal(
    session: Session,
    proposal_id: str,
) -> CapabilityImplementationPlanModel | None:
    return (
        session.query(CapabilityImplementationPlanModel)
        .filter(CapabilityImplementationPlanModel.proposal_id == proposal_id)
        .first()
    )


def implementation_plan_summary(proposal: CapabilityProposalModel) -> str:
    return (
        f"Plan implementation of `{proposal.suggested_capability_id}` as bounded task type "
        f"`{proposal.suggested_task_type}`. This plan is engineering backlog only and does not create "
        "tasks, approvals, runs, or Yggdrasil requests."
    )


def implementation_plan_files(proposal: CapabilityProposalModel) -> list[str]:
    task_type = proposal.suggested_task_type
    return [
        "configs/capabilities.yaml",
        f"configs/task_templates/{task_type}.yaml",
        "automation-api/app/schemas.py",
        "automation-api/tests/test_capability_gateway.py",
        f"automation-worker/worker/handlers/{task_type}.py",
        f"automation-worker/tests/test_{task_type}.py",
        "docs/TASK_SCHEMA.md",
        "docs/BRAGI_HEIMDAL_INTEGRATION.md",
    ]


def implementation_plan_decisions(proposal: CapabilityProposalModel) -> list[str]:
    decisions = [str(item) for item in proposal.required_inputs if str(item).strip()]
    if not decisions:
        decisions = ["exact trigger or schedule", "approved data source or integration", "whitelisted output target"]
    decisions.append(f"whether `{proposal.suggested_capability_id}` should remain L1 or require a higher approval level")
    return dedupe_list(decisions)


def implementation_plan_boundaries(proposal: CapabilityProposalModel) -> list[str]:
    boundaries = [str(item) for item in [*proposal.safety_rules, *proposal.non_goals] if str(item).strip()]
    boundaries.extend(
        [
            "must not expose admin API keys to Bragi, Yggdrasil, Open WebUI, Discord, or task YAML",
            "must not create tasks, approvals, runs, or Yggdrasil requests during implementation planning",
            "must fail closed when required approved IDs or credentials are absent",
        ]
    )
    return dedupe_list(boundaries)


def implementation_plan_tests(proposal: CapabilityProposalModel) -> list[str]:
    capability_id = proposal.suggested_capability_id
    task_type = proposal.suggested_task_type
    return [
        f"`{capability_id}` appears in the explicit capability registry only after implementation",
        f"unknown or unapproved inputs for `{task_type}` are rejected",
        "dry-run mode records the intended result without sending Discord",
        "live notifications use only whitelisted output targets",
        "handler failures are recorded without crashing the worker",
        "run logs redact secret-looking keys and values",
        "Bragi can report the proposal/plan status through read-only context",
    ]


def dedupe_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        cleaned = item.strip()[:300]
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def append_review_note(existing: str, reason: str) -> str:
    reason = reason.strip()
    if not reason:
        return existing
    if not existing:
        return reason[:2000]
    return f"{existing.rstrip()}\n\nClose reason: {reason}"[:2000]


def capability_proposal_to_dict(
    proposal: CapabilityProposalModel,
    implementation_plan: CapabilityImplementationPlanModel | None = None,
) -> dict[str, Any]:
    return redact_secrets(
        {
            "id": proposal.id,
            "status": proposal.status,
            "requested_by": proposal.requested_by,
            "source_channel": proposal.source_channel,
            "title": proposal.title,
            "original_request_preview": proposal.original_request_preview,
            "purpose": proposal.purpose,
            "suggested_capability_id": proposal.suggested_capability_id,
            "suggested_task_type": proposal.suggested_task_type,
            "likely_approval_level": proposal.likely_approval_level,
            "required_inputs": proposal.required_inputs,
            "safety_rules": proposal.safety_rules,
            "non_goals": proposal.non_goals,
            "review_notes": proposal.review_notes,
            "created_at": proposal.created_at,
            "decided_at": proposal.decided_at,
            "implementation_plan": (
                implementation_plan_to_dict(implementation_plan) if implementation_plan is not None else None
            ),
            "execution": {
                "creates_task": False,
                "creates_approval": False,
                "can_be_applied": False,
            },
        }
    )


def implementation_plan_to_dict(plan: CapabilityImplementationPlanModel) -> dict[str, Any]:
    return redact_secrets(
        {
            "id": plan.id,
            "proposal_id": plan.proposal_id,
            "capability_id": plan.capability_id,
            "status": plan.status,
            "created_by": plan.created_by,
            "summary": plan.summary,
            "files_to_change": plan.files_to_change,
            "required_decisions": plan.required_decisions,
            "security_boundaries": plan.security_boundaries,
            "acceptance_tests": plan.acceptance_tests,
            "review_notes": plan.review_notes,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
            "completed_at": plan.completed_at,
            "execution": {
                "creates_task": False,
                "creates_approval": False,
                "can_be_applied": False,
            },
        }
    )


def searchable_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(searchable_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(searchable_text(item) for item in value)
    return re.sub(r"\s+", " ", str(value or "").lower())
