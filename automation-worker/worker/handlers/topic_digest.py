from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from html import unescape

from worker.clients.rss_client import fetch_rss


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


def parse_feed_items(feed_text: str, source_url: str, limit: int) -> list[dict]:
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
        items.append(
            {
                "title": title,
                "summary": summary,
                "link": link,
                "published": published,
                "source": source_url,
                "type": "rss",
            }
        )
    return items


def item_matches_filters(item: dict, filters: dict) -> bool:
    haystack = " ".join(str(item.get(key, "")) for key in ("title", "summary", "source")).lower()
    include = [str(value).lower() for value in filters.get("include", []) if str(value).strip()]
    exclude = [str(value).lower() for value in filters.get("exclude", []) if str(value).strip()]
    if include and not any(term in haystack for term in include):
        return False
    return not any(term in haystack for term in exclude)


def collect_items(task_config: dict, rss_fetcher: Callable[[str, int], str] = fetch_rss) -> tuple[list[dict], list[dict]]:
    policy = task_config.get("policy", {})
    filters = task_config.get("filters", {})
    sources = task_config.get("sources", [])
    max_items = int(policy.get("max_items", 10))
    items: list[dict] = []
    errors: list[dict] = []

    for source in sources:
        if len(items) >= max_items:
            break
        source_type = source.get("type")
        remaining = max_items - len(items)
        if source_type == "rss":
            url = source.get("url", "")
            try:
                feed_text = rss_fetcher(url, int(task_config.get("runtime", {}).get("timeout_seconds", 120)))
                for item in parse_feed_items(feed_text, url, remaining * 2):
                    if item_matches_filters(item, filters):
                        items.append(item)
                    if len(items) >= max_items:
                        break
            except Exception as exc:
                errors.append({"source": url, "error": exc.__class__.__name__})
        elif source_type == "web_query":
            query = source.get("query", "")
            item = {
                "title": "Web query configured",
                "summary": query,
                "link": "",
                "published": "",
                "source": query,
                "type": "web_query",
            }
            if item_matches_filters(item, filters):
                items.append(item)
        else:
            errors.append({"source": source_type, "error": "unsupported_source_type"})

    return items[:max_items], errors


def render_digest(task_config: dict, items: list[dict], errors: list[dict]) -> str:
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
            lines.append(f"{index}. {item.get('title', 'Untitled item')} - {summary} ({source})")
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

    source_refs = [item.get("link") or item.get("source") for item in items if item.get("link") or item.get("source")]
    lines.extend(["", "Sources: " + (", ".join(source_refs[:10]) if source_refs else "none")])
    return "\n".join(lines)


def run_topic_digest(task_config: dict, rss_fetcher: Callable[[str, int], str] = fetch_rss) -> dict:
    policy = task_config.get("policy", {})
    sources = task_config.get("sources", [])
    if policy.get("require_sources", True) and not sources:
        raise ValueError("topic digest requires at least one source")

    items, errors = collect_items(task_config, rss_fetcher=rss_fetcher)
    dry_run = task_config.get("runtime", {}).get("dry_run", True)

    return {
        "status": "dry_run" if dry_run else "ready",
        "title": task_config.get("name", "Topic digest"),
        "items": items,
        "errors": errors,
        "message": render_digest(task_config, items, errors),
        "source_count": len(sources),
    }
