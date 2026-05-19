from __future__ import annotations

import ipaddress
import secrets
import uuid
from typing import Any
from urllib.parse import urlparse

import yaml
from sqlalchemy.orm import Session

from app.models import SourceProposalModel, utcnow
from app.policy import load_policy, load_source_registry
from app.schemas import ApprovedSourceConfig, SourceProposalCreate
from app.services.approval_service import hash_nonce
from app.services.validation_service import find_secret_paths, redact_secrets


class SourceProposalError(ValueError):
    pass


def create_source_proposal(
    session: Session,
    payload: SourceProposalCreate,
) -> tuple[SourceProposalModel, str]:
    source = payload.source
    validate_source_proposal(source)
    nonce = secrets.token_urlsafe(18)
    proposal = SourceProposalModel(
        id=str(uuid.uuid4()),
        source_id=source.id,
        status="pending",
        requested_by=payload.requested_by,
        summary=payload.summary or f"Approved source proposal for {source.id}: {source.name}",
        source_config=redact_secrets(source.model_dump(mode="json")),
        risk=source_risk(source),
        nonce_hash=hash_nonce(nonce),
    )
    session.add(proposal)
    return proposal, nonce


def validate_source_proposal(source: ApprovedSourceConfig) -> None:
    errors: list[str] = []
    if source.type not in {"rss", "http"}:
        errors.append("source proposals may only add rss or http sources")
    if source.query:
        errors.append("source proposals must not include broad web_query/query material")
    if not source.url:
        errors.append("source URL is required")
    if source.url:
        errors.extend(validate_public_https_source_url(source.url))
    if source.ingestion_mode not in {"feed_metadata", "http_summary", "metadata_only"}:
        errors.append("source ingestion_mode is invalid")
    if source.type == "rss" and source.ingestion_mode != "feed_metadata":
        errors.append("rss sources must use feed_metadata ingestion mode")
    if source.type == "http" and source.ingestion_mode == "feed_metadata":
        errors.append("http sources may not use feed_metadata ingestion mode")
    if find_secret_paths(source.model_dump(mode="json")):
        errors.append("plain-text secret-like values found in source proposal")

    registry = load_source_registry(load_policy())
    existing_ids = {item.id for item in registry.sources}
    existing_identities = {(item.type, item.url or item.query or "") for item in registry.sources}
    if source.id in existing_ids:
        errors.append(f"source id already exists in approved registry: {source.id}")
    if (source.type, source.url or source.query or "") in existing_identities:
        errors.append("source URL/query already exists in approved registry")

    if errors:
        raise SourceProposalError("; ".join(errors))


def validate_public_https_source_url(url: str) -> list[str]:
    parsed = urlparse(url)
    errors: list[str] = []
    if parsed.scheme != "https":
        errors.append("source proposal URL must use https")
    if not parsed.hostname:
        errors.append("source proposal URL is missing a hostname")
        return errors
    if parsed.username or parsed.password:
        errors.append("source proposal URL must not include credentials")
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "local"} or host.endswith(".local") or "." not in host:
        errors.append("source proposal URL must use a public hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        errors.append("source proposal URL must not target private or local network addresses")
    return errors


def _mark_source_proposal_approved(proposal: SourceProposalModel) -> None:
    proposal.status = "approved"
    proposal.decided_at = utcnow()


def approve_source_proposal(proposal: SourceProposalModel, nonce: str) -> None:
    if proposal.status != "pending":
        raise SourceProposalError("source proposal is not pending")
    if not secrets.compare_digest(proposal.nonce_hash, hash_nonce(nonce)):
        raise PermissionError("invalid nonce")
    _mark_source_proposal_approved(proposal)


def approve_source_proposal_from_ops(proposal: SourceProposalModel) -> None:
    if proposal.status != "pending":
        raise SourceProposalError("source proposal is not pending")
    _mark_source_proposal_approved(proposal)


def reject_source_proposal(proposal: SourceProposalModel) -> None:
    if proposal.status not in {"pending", "approved"}:
        raise SourceProposalError("source proposal cannot be rejected from its current status")
    proposal.status = "rejected"
    proposal.decided_at = utcnow()


def apply_source_proposal(proposal: SourceProposalModel) -> dict[str, Any]:
    if proposal.status != "approved":
        raise SourceProposalError("source proposal must be approved before apply")
    source = ApprovedSourceConfig.model_validate(proposal.source_config)
    # The API intentionally does not write the checked-in registry from inside
    # the container. Apply means the reviewed YAML entry is generated for the
    # operator to commit through normal repository review.
    proposal.status = "applied"
    proposal.applied_at = utcnow()
    return {
        "source_entry": source.model_dump(mode="json", exclude_none=True),
        "registry_file": "configs/sources/approved_sources.yaml",
        "operator_action": "Add source_entry under sources or to an included registry file, then commit and redeploy.",
        "source_yaml": yaml.safe_dump([source.model_dump(mode="json", exclude_none=True)], sort_keys=False, allow_unicode=True),
    }


def source_proposal_to_dict(proposal: SourceProposalModel, *, nonce: str | None = None) -> dict[str, Any]:
    payload = {
        "id": proposal.id,
        "source_id": proposal.source_id,
        "status": proposal.status,
        "requested_by": proposal.requested_by,
        "summary": proposal.summary,
        "source": redact_secrets(proposal.source_config),
        "risk": proposal.risk,
        "created_at": proposal.created_at,
        "decided_at": proposal.decided_at,
        "applied_at": proposal.applied_at,
    }
    if nonce:
        payload["nonce"] = nonce
    return payload


def source_risk(source: ApprovedSourceConfig) -> dict[str, Any]:
    return {
        "severity": "source_review",
        "requires_admin": True,
        "network_fetch": source.ingestion_mode != "metadata_only",
        "ingestion_mode": source.ingestion_mode,
        "ai_safe_fit": source.ai_safe_fit or "operator_review",
        "notes": [
            "External content remains untrusted data.",
            "Approval adds a selectable source only; it does not approve any task execution.",
        ],
    }
