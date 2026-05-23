from __future__ import annotations

from .goal_loop import (
    classify_automation_request,
    classify_deterministic_automation_request,
    infer_registered_capability,
    match_configured_capability_gap,
    requires_new_monitoring_capability,
    resolve_task,
    resolve_task_reference,
    unsafe_reasons_for_text,
)
from .goal_models import (
    AutomationRequestClassification,
    AutomationRequestKind,
    AutomationTargetKind,
    TaskResolution,
)

__all__ = [
    "AutomationRequestClassification",
    "AutomationRequestKind",
    "AutomationTargetKind",
    "TaskResolution",
    "classify_automation_request",
    "classify_deterministic_automation_request",
    "infer_registered_capability",
    "match_configured_capability_gap",
    "requires_new_monitoring_capability",
    "resolve_task",
    "resolve_task_reference",
    "unsafe_reasons_for_text",
]
