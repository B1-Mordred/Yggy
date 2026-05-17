from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import ApiRole, require_roles
from app.database import get_session
from app.routers.tasks import create_draft_task_record
from app.schemas import TaskTemplateRenderRequest
from app.services.task_template_service import (
    TemplateError,
    UnknownTemplateError,
    get_template,
    load_templates,
    render_task_from_template,
)
from app.services.validation_service import redact_secrets

router = APIRouter(prefix="/task-templates", tags=["task-templates"])


@router.get("")
def list_task_templates(
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN, ApiRole.WORKER)),
) -> list[dict]:
    return [template.summary().model_dump(mode="json") for template in load_templates().values()]


@router.get("/{template_id}")
def get_task_template(
    template_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN, ApiRole.WORKER)),
) -> dict:
    try:
        template = get_template(template_id)
    except UnknownTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    payload = template.summary().model_dump(mode="json")
    payload["defaults"] = redact_secrets(template.defaults)
    payload["default_source_ids"] = template.default_source_ids
    return payload


@router.post("/{template_id}/draft", status_code=status.HTTP_201_CREATED)
def draft_task_from_template(
    template_id: str,
    payload: TaskTemplateRenderRequest,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    try:
        task_config = render_task_from_template(template_id, payload)
        template = get_template(template_id)
    except UnknownTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = create_draft_task_record(
        task_config,
        role=role,
        session=session,
        audit_details={"template_id": template_id},
    )
    result["template"] = template.summary().model_dump(mode="json")
    result["rendered_config"] = redact_secrets(task_config.model_dump(mode="json"))
    return result
