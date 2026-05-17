from __future__ import annotations

import csv
import os
import re
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
    description: str = ""
    region: str = ""
    languages: list[str] = field(default_factory=list)
    source_type_label: str = ""
    update_cadence: str = ""
    ingestion_notes: str = ""
    ai_safe_fit: str = ""
    ingestion_mode: str = "feed_metadata"

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
            "description": self.description,
            "region": self.region,
            "languages": list(self.languages),
            "source_type_label": self.source_type_label,
            "update_cadence": self.update_cadence,
            "ingestion_notes": self.ingestion_notes,
            "ai_safe_fit": self.ai_safe_fit,
            "ingestion_mode": self.ingestion_mode,
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
        sources = [source_from_dict(item) for item in load_source_rows(registry_path, visited=set()) if isinstance(item, dict)]
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


def load_source_rows(path: Path, *, visited: set[Path]) -> list[dict[str, Any]]:
    resolved = path.resolve()
    if resolved in visited:
        raise ValueError(f"recursive source registry include: {path}")
    visited.add(resolved)
    if path.suffix.lower() == ".tsv":
        return source_rows_from_tsv(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = [item for item in data.get("sources", []) if isinstance(item, dict)]
    for include_file in data.get("include_files", []) or []:
        include_path = path.parent / str(include_file)
        rows.extend(load_source_rows(include_path, visited=visited))
    return rows


def source_from_dict(item: dict[str, Any]) -> ApprovedSource:
    return ApprovedSource(
        id=str(item.get("id", "")),
        name=str(item.get("name") or item.get("id") or ""),
        type=str(item.get("type", "")),
        url=item.get("url"),
        query=item.get("query"),
        categories=[str(category) for category in item.get("categories", [])],
        trust_level=str(item.get("trust_level", "approved")),
        enabled=bool(item.get("enabled", True)),
        max_items=item.get("max_items"),
        description=str(item.get("description", "")),
        region=str(item.get("region", "")),
        languages=[str(language) for language in item.get("languages", [])],
        source_type_label=str(item.get("source_type_label", "")),
        update_cadence=str(item.get("update_cadence", "")),
        ingestion_notes=str(item.get("ingestion_notes", "")),
        ai_safe_fit=str(item.get("ai_safe_fit", "")),
        ingestion_mode=str(item.get("ingestion_mode", "feed_metadata")),
    )


def source_rows_from_tsv(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in first_line else "|"
    rows = csv.DictReader(text.splitlines(), delimiter=delimiter)
    sources: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for row in rows:
        if not row or not row.get("Title") or not row.get("URL"):
            continue
        source_id = unique_source_id(slug_source_id(str(row["Title"])), used_ids)
        used_ids.add(source_id)
        ai_safe_fit = str(row.get("AI-safe fit") or "").strip()
        source_type_label = str(row.get("Source type") or "").strip()
        url = str(row["URL"]).strip()
        sources.append(
            {
                "id": source_id,
                "name": str(row["Title"]).strip(),
                "type": source_config_type(source_type_label, url),
                "url": url,
                "categories": source_categories_from_row(row),
                "trust_level": trust_level_from_ai_fit(ai_safe_fit),
                "enabled": True,
                "max_items": 5,
                "description": str(row.get("Brief description") or "").strip(),
                "region": str(row.get("Region") or "").strip(),
                "languages": language_values(str(row.get("Language(s)") or "")),
                "source_type_label": source_type_label,
                "update_cadence": str(row.get("Update cadence") or "").strip(),
                "ingestion_notes": str(row.get("Ingestion / license notes") or "").strip(),
                "ai_safe_fit": ai_safe_fit,
                "ingestion_mode": ingestion_mode_from_row(source_type_label, url, ai_safe_fit),
            }
        )
    return sources


def source_config_type(source_type_label: str, url: str) -> str:
    haystack = f"{source_type_label} {url}".lower()
    if "rss" in haystack or "feed" in haystack or url.endswith((".rss", ".rdf", ".xml")):
        return "rss"
    return "http"


def ingestion_mode_from_row(source_type_label: str, url: str, ai_safe_fit: str) -> str:
    if source_config_type(source_type_label, url) == "rss":
        return "feed_metadata"
    if ai_safe_fit.strip().startswith("A"):
        return "http_summary"
    return "metadata_only"


def trust_level_from_ai_fit(ai_safe_fit: str) -> str:
    if ai_safe_fit.strip().startswith("A"):
        return "ai_safe_a_open"
    if ai_safe_fit.strip().startswith("C"):
        return "ai_safe_c_metadata_only"
    return "ai_safe_b_terms_check"


def source_categories_from_row(row: dict[str, str]) -> list[str]:
    values: list[str] = ["preapproved"]
    for key in ("Category", "Subcategory"):
        values.extend(slug_source_id(part) for part in re.split(r"[:/]", str(row.get(key) or "")) if part.strip())
    for value in language_values(str(row.get("Language(s)") or "")):
        values.append(f"language_{slug_source_id(value)}")
    for value in re.split(r"[/,]", str(row.get("Region") or "")):
        if value.strip():
            values.append(f"region_{slug_source_id(value)}")
    deduped: list[str] = []
    for value in values:
        if len(value) >= 3 and value not in deduped:
            deduped.append(value[:127])
    return deduped


def language_values(value: str) -> list[str]:
    normalized = value.replace("+", "/").replace("regional editions", "regional")
    return [item.strip() for item in re.split(r"[/,]", normalized) if item.strip()]


def slug_source_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug or len(slug) < 3:
        slug = "source"
    if not re.match(r"^[a-z0-9]", slug):
        slug = f"source_{slug}"
    return slug[:120]


def unique_source_id(source_id: str, used_ids: set[str]) -> str:
    if source_id not in used_ids:
        return source_id
    for suffix in range(2, 1000):
        candidate = f"{source_id[:115]}_{suffix}"
        if candidate not in used_ids:
            return candidate
    raise ValueError(f"could not generate unique source id for {source_id}")
