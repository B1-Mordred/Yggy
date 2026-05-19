from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class AutomationRequestKind(str, Enum):
    CHAT = "chat"
    HELP = "help"
    LIST_EXISTING = "list_existing"
    INSPECT_EXISTING = "inspect_existing"
    RUN_EXISTING = "run_existing"
    PAUSE_EXISTING = "pause_existing"
    MODIFY_EXISTING = "modify_existing"
    CREATE_NEW = "create_new"
    PROPOSE_NEW_CAPABILITY = "propose_new_capability"
    UNSAFE = "unsafe"
    NEEDS_CLARIFICATION = "needs_clarification"


class AutomationTargetKind(str, Enum):
    EXISTING_TASK = "existing_task"
    NEW_TASK = "new_task"
    NEW_CAPABILITY = "new_capability"
    UNKNOWN = "unknown"


class TaskResolution(BaseModel):
    status: Literal["none", "single", "multiple"] = "none"
    task_id: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = ""


class AutomationRequestClassification(BaseModel):
    request_kind: AutomationRequestKind
    target_kind: AutomationTargetKind = AutomationTargetKind.UNKNOWN
    target_task_id: str | None = None
    target_task_candidates: list[str] = Field(default_factory=list)
    capability_id: str | None = None
    operation: dict[str, Any] | None = None
    candidate_intent: dict[str, Any] | None = None
    missing_information: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    unsafe_reasons: list[str] = Field(default_factory=list)


GOAL_CAPABILITY_IDS = {
    "server_health.v1",
    "topic_digest.v1",
    "topic_digest.modify_subjects.v1",
    "printer_supply_status.v1",
    "n8n_webhook.v1",
}
