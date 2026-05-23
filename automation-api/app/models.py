from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_level: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class TaskConfigVersionModel(Base):
    __tablename__ = "task_config_versions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_task_config_versions_task_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TopicModel(Base):
    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ApprovalModel(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    approval_level: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskChangeProposalModel(Base):
    __tablename__ = "task_change_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_level: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    risk: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    diff: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    base_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    proposed_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceProposalModel(Base):
    __tablename__ = "source_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    risk: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    nonce_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityProposalModel(Base):
    __tablename__ = "capability_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_channel: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_request_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_capability_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    suggested_task_type: Mapped[str] = mapped_column(String(128), nullable=False)
    likely_approval_level: Mapped[str] = mapped_column(String(64), nullable=False)
    required_inputs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    safety_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    non_goals: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityImplementationPlanModel(Base):
    __tablename__ = "capability_implementation_plans"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id",
            name="uq_capability_implementation_plans_proposal_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(64), ForeignKey("capability_proposals.id"), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="implementation_planned", nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="ops_dashboard")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    files_to_change: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_decisions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    security_boundaries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    acceptance_tests: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityImplementationRunModel(Base):
    __tablename__ = "capability_implementation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(64), ForeignKey("capability_proposals.id"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("capability_implementation_plans.id"), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    branch: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    test_results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="ops_dashboard")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityGapModel(Base):
    __tablename__ = "capability_gaps"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), default="ops_dashboard", nullable=False)
    route: Mapped[str] = mapped_column(String(64), default="propose_new_capability", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_capability_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    suggested_task_type: Mapped[str] = mapped_column(String(128), nullable=False)
    likely_approval_level: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    exclude_terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_inputs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    safety_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    non_goals: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    linked_capability_proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ChannelNotificationModel(Base):
    __tablename__ = "channel_notifications"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_channel_notifications_dedupe_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notification_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunModel(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    log: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HeartbeatModel(Base):
    __tablename__ = "heartbeats"

    service: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ResearchItemModel(Base):
    __tablename__ = "research_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    item_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
