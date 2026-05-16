from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import TopicModel
from app.schemas import TopicConfig
from app.services.validation_service import find_secret_paths, redact_secrets

router = APIRouter(prefix="/topics", tags=["topics"])


def topic_to_dict(topic: TopicModel) -> dict:
    return {
        "id": topic.id,
        "name": topic.name,
        "enabled": topic.enabled,
        "owner": topic.owner,
        "created_by": topic.created_by,
        "config": redact_secrets(topic.config),
        "created_at": topic.created_at,
        "updated_at": topic.updated_at,
    }


@router.get("")
def list_topics(
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> list[dict]:
    return [topic_to_dict(topic) for topic in session.query(TopicModel).order_by(TopicModel.id).all()]


@router.post("/draft", status_code=status.HTTP_201_CREATED)
def draft_topic(
    payload: TopicConfig,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict:
    if find_secret_paths(payload.model_dump(mode="json")):
        raise HTTPException(status_code=422, detail="topic contains secret-like values")
    topic_config = payload.model_copy(update={"enabled": False})
    if session.get(TopicModel, topic_config.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="topic already exists")
    topic = TopicModel(
        id=topic_config.id,
        name=topic_config.name,
        enabled=False,
        owner=topic_config.owner,
        created_by=topic_config.created_by,
        config=topic_config.model_dump(mode="json"),
    )
    session.add(topic)
    audit_event(session, role, "topic.draft", "topic", topic.id)
    session.commit()
    return topic_to_dict(topic)
