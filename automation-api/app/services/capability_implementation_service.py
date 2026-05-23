from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import CapabilityImplementationRunModel, CapabilityProposalModel, utcnow
from app.schemas import CapabilityImplementationRunUpdate
from app.services.capability_proposal_service import implementation_plan_for_proposal
from app.services.validation_service import redact_secrets


class CapabilityImplementationError(ValueError):
    pass


IMPLEMENTATION_RUN_STATUSES = {"queued", "running", "completed", "failed"}
ACTIVE_IMPLEMENTATION_RUN_STATUSES = {"queued", "running"}
TERMINAL_IMPLEMENTATION_RUN_STATUSES = {"completed", "failed"}
IMPLEMENTATION_RUN_TRANSITIONS = {
    "queued": {"running", "failed"},
    "running": {"completed", "failed"},
    "failed": set(),
    "completed": set(),
}


def create_implementation_run(
    session: Session,
    proposal: CapabilityProposalModel,
    *,
    created_by: str = "ops_dashboard",
    reason: str = "",
) -> CapabilityImplementationRunModel:
    if proposal.status != "implementation_planned":
        raise CapabilityImplementationError("capability proposal must be implementation_planned before implementation can be queued")
    plan = implementation_plan_for_proposal(session, proposal.id)
    if not plan or plan.status != "implementation_planned":
        raise CapabilityImplementationError("capability proposal has no active implementation plan")
    active = active_implementation_run_for_proposal(session, proposal.id)
    if active:
        raise CapabilityImplementationError(f"capability proposal already has an active implementation run: {active.id}")

    run = CapabilityImplementationRunModel(
        id=str(uuid.uuid4()),
        proposal_id=proposal.id,
        plan_id=plan.id,
        capability_id=proposal.suggested_capability_id,
        status="queued",
        branch=suggest_implementation_branch(proposal),
        summary=str(
            redact_secrets(
                reason
                or (
                    "Queued for the host-side capability implementation runner. The API only records the run; "
                    "the local runner invokes Hermes, validates the patch, and creates a local commit."
                )
            )
        ),
        test_results={},
        error="",
        created_by=created_by,
    )
    session.add(run)
    return run


def list_implementation_runs(
    session: Session,
    *,
    proposal_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[CapabilityImplementationRunModel]:
    query = session.query(CapabilityImplementationRunModel)
    if proposal_id:
        query = query.filter(CapabilityImplementationRunModel.proposal_id == proposal_id)
    if status:
        query = query.filter(CapabilityImplementationRunModel.status == status)
    return (
        query.order_by(CapabilityImplementationRunModel.created_at.desc(), CapabilityImplementationRunModel.id.asc())
        .limit(limit)
        .all()
    )


def get_implementation_run(session: Session, run_id: str) -> CapabilityImplementationRunModel | None:
    return session.get(CapabilityImplementationRunModel, run_id)


def active_implementation_run_for_proposal(
    session: Session,
    proposal_id: str,
) -> CapabilityImplementationRunModel | None:
    return (
        session.query(CapabilityImplementationRunModel)
        .filter(CapabilityImplementationRunModel.proposal_id == proposal_id)
        .filter(CapabilityImplementationRunModel.status.in_(ACTIVE_IMPLEMENTATION_RUN_STATUSES))
        .order_by(CapabilityImplementationRunModel.created_at.desc())
        .first()
    )


def update_implementation_run(
    run: CapabilityImplementationRunModel,
    payload: CapabilityImplementationRunUpdate,
) -> CapabilityImplementationRunModel:
    new_status = payload.status
    if new_status is not None and new_status != run.status:
        if new_status not in IMPLEMENTATION_RUN_TRANSITIONS.get(run.status, set()):
            raise CapabilityImplementationError(f"implementation run cannot move from {run.status} to {new_status}")
        run.status = new_status

    if payload.branch is not None:
        run.branch = payload.branch
    if payload.commit_sha is not None:
        run.commit_sha = payload.commit_sha
    if payload.summary is not None:
        run.summary = str(redact_secrets(payload.summary))
    if payload.test_results is not None:
        run.test_results = redact_secrets(payload.test_results)
    if payload.error is not None:
        run.error = str(redact_secrets(payload.error))

    if run.status == "completed" and not run.commit_sha:
        raise CapabilityImplementationError("completed implementation runs must record commit_sha")
    if run.status in TERMINAL_IMPLEMENTATION_RUN_STATUSES and run.completed_at is None:
        run.completed_at = utcnow()
    run.updated_at = utcnow()
    return run


def implementation_run_to_dict(run: CapabilityImplementationRunModel) -> dict[str, Any]:
    return redact_secrets(
        {
            "id": run.id,
            "proposal_id": run.proposal_id,
            "plan_id": run.plan_id,
            "capability_id": run.capability_id,
            "status": run.status,
            "branch": run.branch,
            "commit_sha": run.commit_sha,
            "summary": run.summary,
            "test_results": run.test_results,
            "error": run.error,
            "created_by": run.created_by,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "completed_at": run.completed_at,
            "operator_handoff": {
                "cli_command": f"python scripts/implement_capability_plan.py --run-id {run.id}",
                "runner_command": "python scripts/capability_implementation_runner.py --once",
                "queued_only": run.status == "queued",
                "requires_host_cli": True,
                "requires_host_runner": True,
                "runner_picks_up_queued_runs": True,
            },
            "execution": {
                "creates_task": False,
                "creates_approval": False,
                "can_run_automation": False,
                "can_push": False,
                "local_commit_only": True,
            },
        }
    )


def suggest_implementation_branch(proposal: CapabilityProposalModel) -> str:
    task_type = proposal.suggested_task_type or "capability"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", task_type).strip("-").lower() or "capability"
    return f"capability/{slug}-{proposal.id[:8]}"
