from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth import ApiRole, require_roles
from app.database import get_session
from app.models import ResearchItemModel
from app.schemas import ResearchQueryRequest
from app.services.research_service import (
    ResearchError,
    list_approved_sources,
    query_research,
    research_item_to_dict,
)
from app.services.validation_service import redact_secrets

sources_router = APIRouter(prefix="/sources", tags=["sources"])
research_router = APIRouter(prefix="/research", tags=["research"])


@sources_router.get("")
def list_sources(
    include_disabled: bool = False,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
) -> list[dict[str, Any]]:
    return list_approved_sources(include_disabled=include_disabled)


@research_router.post("/query")
def research_query(
    payload: ResearchQueryRequest,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        result = query_research(session, payload)
    except ResearchError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    audit_event(
        session,
        role,
        "research.query",
        "research",
        "approved_sources",
        {
            "source_ids": result.get("source_ids", []),
            "item_count": result.get("item_count", 0),
            "error_count": len(result.get("errors", [])) if isinstance(result.get("errors"), list) else 0,
            "query_preview": redact_secrets(payload.query or "")[:240],
        },
    )
    session.commit()
    return result


@research_router.get("/items")
def list_research_items(
    source_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = session.query(ResearchItemModel)
    if source_id:
        query = query.filter(ResearchItemModel.source_id == source_id)
    items = query.order_by(ResearchItemModel.fetched_at.desc(), ResearchItemModel.id.asc()).limit(limit).all()
    return [research_item_to_dict(item) for item in items]


@research_router.get("/items/{item_id}")
def get_research_item(
    item_id: str,
    role: ApiRole = Depends(require_roles(ApiRole.TOOL, ApiRole.ADMIN)),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = session.get(ResearchItemModel, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="research item not found")
    return research_item_to_dict(item)
