from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import CapabilityProposalModel, utcnow
from app.schemas import CapabilityProposalCreate
from app.services.capability_gateway import CapabilityError, get_capability
from app.services.validation_service import find_secret_paths, redact_secrets


class CapabilityProposalError(ValueError):
    pass


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
    if proposal.status != "pending":
        raise CapabilityProposalError("capability proposal is not pending")
    if status not in {"accepted", "rejected", "closed"}:
        raise CapabilityProposalError("invalid capability proposal close status")
    proposal.status = status
    proposal.decided_at = utcnow()
    if reason:
        proposal.review_notes = append_review_note(proposal.review_notes, reason)


def append_review_note(existing: str, reason: str) -> str:
    reason = reason.strip()
    if not reason:
        return existing
    if not existing:
        return reason[:2000]
    return f"{existing.rstrip()}\n\nClose reason: {reason}"[:2000]


def capability_proposal_to_dict(proposal: CapabilityProposalModel) -> dict[str, Any]:
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
