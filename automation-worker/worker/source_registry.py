from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SOURCE_REGISTRY_FILE = "configs/sources/approved_sources.yaml"


@dataclass(frozen=True)
class ApprovedSource:
    id: str
    name: str
    type: str
    url: str | None = None
    query: str | None = None
    categories: list[str] = field(default_factory=list)
    trust_level: str = "approved"
    enabled: bool = True
    max_items: int | None = None

    @property
    def identity(self) -> tuple[str, str]:
        if self.type in {"rss", "http"}:
            return self.type, self.url or ""
        if self.type == "web_query":
            return self.type, self.query or ""
        return self.type, self.url or self.query or ""

    def public_metadata(self) -> dict:
        return {
            "source_id": self.id,
            "name": self.name,
            "type": self.type,
            "url": self.url,
            "query": self.query,
            "categories": list(self.categories),
            "trust_level": self.trust_level,
            "enabled": self.enabled,
            "max_items": self.max_items,
        }


@dataclass(frozen=True)
class SourceApproval:
    approved: ApprovedSource | None
    error: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.approved is not None and self.error is None


class SourceRegistry:
    def __init__(self, sources: list[ApprovedSource] | None = None) -> None:
        self.sources = sources or []
        self.by_id = {source.id: source for source in self.sources}
        self.enabled_by_id = {source.id: source for source in self.sources if source.enabled}
        self.enabled_by_identity = {source.identity: source for source in self.sources if source.enabled}

    @classmethod
    def from_env(cls) -> "SourceRegistry":
        return cls.from_file(os.getenv("AUTOMATION_SOURCE_REGISTRY_FILE", DEFAULT_SOURCE_REGISTRY_FILE))

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "SourceRegistry":
        registry_path = Path(path)
        if not registry_path.exists():
            return cls([])
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        sources = [
            ApprovedSource(
                id=str(item.get("id", "")),
                name=str(item.get("name") or item.get("id") or ""),
                type=str(item.get("type", "")),
                url=item.get("url"),
                query=item.get("query"),
                categories=[str(category) for category in item.get("categories", [])],
                trust_level=str(item.get("trust_level", "approved")),
                enabled=bool(item.get("enabled", True)),
                max_items=item.get("max_items"),
            )
            for item in data.get("sources", [])
            if isinstance(item, dict)
        ]
        return cls(sources)

    def approve(self, source: dict[str, Any]) -> SourceApproval:
        source_id = source.get("source_id")
        if not source_id:
            return SourceApproval(None, "source_id_required", "task source must reference an approved source_id")

        approved = self.by_id.get(str(source_id))
        if approved is None:
            return SourceApproval(None, "source_not_approved", f"source_id {source_id} is not in the registry")
        if not approved.enabled:
            return SourceApproval(approved, "source_disabled", f"source_id {source_id} is disabled")
        if source_identity(source) != approved.identity:
            return SourceApproval(approved, "source_identity_mismatch", f"source_id {source_id} does not match registry")
        return SourceApproval(approved)


def source_identity(source: dict[str, Any]) -> tuple[str, str]:
    source_type = str(source.get("type", ""))
    if source_type in {"rss", "http"}:
        return source_type, str(source.get("url") or "")
    if source_type == "web_query":
        return source_type, str(source.get("query") or "")
    return source_type, str(source.get("url") or source.get("query") or "")
