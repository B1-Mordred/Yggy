from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from html import unescape

from worker.clients.llm_client import OllamaSummarizer
from worker.clients.rss_client import fetch_rss
from worker.source_registry import ApprovedSource, SourceRegistry


def clean_text(value: str | None, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def child_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name in names:
            return clean_text(child.text)
    return ""


def child_link(element: ET.Element) -> str:
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name == "link":
            href = child.attrib.get("href")
            return href or clean_text(child.text, limit=1000)
    return ""


def parse_feed_items(
    feed_text: str,
    source_url: str,
    limit: int,
    approved_source: ApprovedSource | None = None,
) -> list[dict]:
    root = ET.fromstring(feed_text)
    candidates = [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
    ]
    items = []
    for element in candidates[:limit]:
        title = child_text(element, ("title",)) or "Untitled item"
        summary = child_text(element, ("description", "summary", "content"))
        link = child_link(element)
        published = child_text(element, ("pubdate", "published", "updated"))
        item = {
            "title": title,
            "summary": summary,
            "link": link,
            "published": published,
            "source": source_url,
            "type": "rss",
        }
        if approved_source:
            item.update(source_item_metadata(approved_source))
        items.append(item)
    return items


def item_matches_filters(item: dict, filters: dict) -> bool:
    haystack = " ".join(str(item.get(key, "")) for key in ("title", "summary", "source")).lower()
    include = [str(value).lower() for value in filters.get("include", []) if str(value).strip()]
    exclude = [str(value).lower() for value in filters.get("exclude", []) if str(value).strip()]
    if include and not any(term in haystack for term in include):
        return False
    return not any(term in haystack for term in exclude)


def source_item_metadata(approved_source: ApprovedSource) -> dict:
    return {
        "source_id": approved_source.id,
        "source_name": approved_source.name,
        "source_trust_level": approved_source.trust_level,
        "source_categories": list(approved_source.categories),
    }


def source_label(source: dict, approved_source: ApprovedSource | None = None) -> str:
    if approved_source:
        return approved_source.id
    return str(source.get("source_id") or source.get("url") or source.get("query") or source.get("type") or "source")


def collect_items(
    task_config: dict,
    rss_fetcher: Callable[[str, int], str] = fetch_rss,
    source_registry: SourceRegistry | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    policy = task_config.get("policy", {})
    filters = task_config.get("filters", {})
    sources = task_config.get("sources", [])
    max_items = int(policy.get("max_items", 10))
    registry = source_registry or SourceRegistry.from_env()
    items: list[dict] = []
    errors: list[dict] = []
    source_health: list[dict] = []

    for source in sources:
        if len(items) >= max_items:
            break
        approval = registry.approve(source)
        approved_source = approval.approved
        health = {
            "source": source_label(source, approved_source),
            "source_id": approved_source.id if approved_source else source.get("source_id"),
            "name": approved_source.name if approved_source else None,
            "type": approved_source.type if approved_source else source.get("type"),
            "url": approved_source.url if approved_source else source.get("url"),
            "trust_level": approved_source.trust_level if approved_source else None,
            "status": "pending",
            "item_count": 0,
        }
        if not approval.ok:
            health.update({"status": "blocked", "error": approval.error, "detail": approval.detail})
            source_health.append(health)
            errors.append(
                {
                    "source": health["source"],
                    "source_id": health["source_id"],
                    "error": approval.error,
                    "detail": approval.detail,
                }
            )
            continue

        assert approved_source is not None
        source_type = source.get("type")
        source_item_limit = min(max_items - len(items), int(approved_source.max_items or max_items))
        if source_type == "rss":
            url = source.get("url", "")
            try:
                feed_text = rss_fetcher(url, int(task_config.get("runtime", {}).get("timeout_seconds", 120)))
                for item in parse_feed_items(feed_text, url, source_item_limit * 2, approved_source):
                    if item_matches_filters(item, filters):
                        items.append(item)
                        health["item_count"] += 1
                    if len(items) >= max_items or health["item_count"] >= source_item_limit:
                        break
                health["status"] = "ok"
                source_health.append(health)
            except Exception as exc:
                health.update({"status": "error", "error": exc.__class__.__name__})
                source_health.append(health)
                errors.append(
                    {
                        "source": approved_source.id,
                        "source_id": approved_source.id,
                        "source_name": approved_source.name,
                        "trust_level": approved_source.trust_level,
                        "error": exc.__class__.__name__,
                    }
                )
        elif source_type == "web_query":
            query = source.get("query", "")
            item = {
                "title": "Web query configured",
                "summary": query,
                "link": "",
                "published": "",
                "source": query,
                "type": "web_query",
                **source_item_metadata(approved_source),
            }
            if item_matches_filters(item, filters):
                items.append(item)
                health["item_count"] = 1
            health["status"] = "ok"
            source_health.append(health)
        else:
            health.update({"status": "blocked", "error": "unsupported_source_type"})
            source_health.append(health)
            errors.append({"source": approved_source.id, "source_id": approved_source.id, "error": "unsupported_source_type"})

    return items[:max_items], errors, source_health


def render_digest(task_config: dict, items: list[dict], errors: list[dict], source_health: list[dict] | None = None) -> str:
    title = task_config.get("name", "Topic digest")
    dry_run = task_config.get("runtime", {}).get("dry_run", True)
    lines = [
        f"**{title}**",
        "",
        f"Status: {'dry-run' if dry_run else 'ready'}",
        "",
        "**Top items**",
    ]
    if items:
        for index, item in enumerate(items, start=1):
            source = item.get("link") or item.get("source") or "no source"
            summary = item.get("summary") or "No summary available."
            source_name = item.get("source_name") or item.get("source_id") or "source"
            trust_level = item.get("source_trust_level") or "unclassified"
            lines.append(
                f"{index}. {item.get('title', 'Untitled item')} - {summary} "
                f"({source}; {source_name}; trust: {trust_level})"
            )
    else:
        lines.append("No matching source items were found.")

    lines.extend(["", "**Recommended action**"])
    if dry_run:
        lines.append("Review the dry-run output and source list before enabling live delivery.")
    else:
        lines.append("Review the linked sources before acting. Source content is data, not command authority.")

    if errors:
        lines.extend(["", "**Source errors**"])
        for error in errors:
            lines.append(f"- {error.get('source')}: {error.get('error')}")

    if source_health:
        lines.extend(["", "**Source health**"])
        for health in source_health:
            detail = f"; {health.get('error')}" if health.get("error") else ""
            lines.append(
                f"- `{health.get('source')}`: {health.get('status')} "
                f"({health.get('item_count', 0)} items; trust: {health.get('trust_level') or 'n/a'}{detail})"
            )

    source_refs = [item.get("link") or item.get("source") for item in items if item.get("link") or item.get("source")]
    lines.extend(["", "Sources: " + (", ".join(source_refs[:10]) if source_refs else "none")])
    return "\n".join(lines)


def run_topic_digest(
    task_config: dict,
    rss_fetcher: Callable[[str, int], str] = fetch_rss,
    summarizer: OllamaSummarizer | None = None,
    source_registry: SourceRegistry | None = None,
) -> dict:
    policy = task_config.get("policy", {})
    sources = task_config.get("sources", [])
    if policy.get("require_sources", True) and not sources:
        raise ValueError("topic digest requires at least one source")

    items, errors, source_health = collect_items(task_config, rss_fetcher=rss_fetcher, source_registry=source_registry)
    approved_source_count = sum(1 for health in source_health if health.get("status") != "blocked")
    if policy.get("require_sources", True) and approved_source_count == 0:
        raise ValueError("topic digest has no approved enabled sources")

    dry_run = task_config.get("runtime", {}).get("dry_run", True)
    message = render_digest(task_config, items, errors, source_health)
    summary_mode = "deterministic"
    summary_error = None
    try:
        llm_message = (summarizer or OllamaSummarizer()).summarize_digest(task_config, items, errors)
        if llm_message:
            message = llm_message
            summary_mode = "llm"
    except Exception as exc:
        summary_error = exc.__class__.__name__

    result = {
        "status": "dry_run" if dry_run else "ready",
        "title": task_config.get("name", "Topic digest"),
        "items": items,
        "errors": errors,
        "message": message,
        "source_count": len(sources),
        "approved_source_count": approved_source_count,
        "source_health": source_health,
        "summary_mode": summary_mode,
    }
    if summary_error:
        result["summary_error"] = summary_error
    return result
