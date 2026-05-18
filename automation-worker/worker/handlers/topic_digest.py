from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from html import unescape

from worker.clients.http_client import fetch_text
from worker.clients.llm_client import OllamaSummarizer
from worker.clients.rss_client import fetch_rss
from worker.source_registry import ApprovedSource, SourceRegistry


SECTIONED_DIGEST_MARKERS = (
    "ai security news",
    "ai hardware news",
    "ai software news",
    "security issues related to this system",
    "eu political news",
    "german political news",
)

SECTION_RULES = [
    {
        "title": "AI Security News",
        "source_ids": {"google_security_blog", "microsoft_security_blog"},
        "keywords": {
            "ai",
            "artificial intelligence",
            "llm",
            "gemini",
            "agent",
            "prompt injection",
            "model",
            "adversarial",
            "security",
            "vulnerability",
            "threat",
        },
        "required_groups": (
            {"ai", "artificial intelligence", "llm", "gemini", "agent", "prompt injection", "model"},
            {"security", "vulnerability", "threat", "abuse", "prompt injection", "adversarial"},
        ),
    },
    {
        "title": "AI Hardware News",
        "source_ids": {"nvidia_developer_blog", "nvidia_news_releases"},
        "keywords": {
            "gpu",
            "hardware",
            "accelerator",
            "nvidia",
            "cuda",
            "blackwell",
            "rubin",
            "data center",
            "supercomputer",
            "inference",
        },
        "required_groups": (),
    },
    {
        "title": "AI Software News",
        "source_ids": {"openai_news", "google_ai_blog", "nvidia_developer_blog", "open_webui_releases", "ollama_releases"},
        "keywords": {
            "ai",
            "model",
            "llm",
            "software",
            "release",
            "api",
            "agent",
            "open webui",
            "ollama",
            "n8n",
        },
        "required_groups": (),
    },
    {
        "title": "System Component Security Issues",
        "source_ids": {
            "ubuntu_security_notices_rss",
            "debian_security_advisories",
            "open_webui_releases",
            "ollama_releases",
            "n8n_releases",
            "docker_blog",
            "cisa_cybersecurity_advisories",
            "cisa_known_exploited_vulnerabilities_catalog",
        },
        "keywords": {
            "ubuntu",
            "debian",
            "open webui",
            "ollama",
            "hermes",
            "docker",
            "n8n",
            "yggy",
            "cve",
            "vulnerability",
            "security",
            "exploit",
            "patch",
            "advisory",
        },
        "required_groups": (),
    },
    {
        "title": "EU Political News",
        "source_ids": {
            "european_parliament_press_releases_rss",
            "european_commission_press_corner_rss",
            "deutsche_welle_english_rss",
        },
        "keywords": {
            "eu",
            "european",
            "commission",
            "parliament",
            "council",
            "brussels",
            "europarl",
            "europa",
        },
        "required_groups": (),
    },
    {
        "title": "German Political News",
        "source_ids": {
            "tagesschau_rss_alle_meldungen",
            "deutsche_welle_german_rss",
            "der_spiegel_rss",
            "zeit_online_news_rss",
            "netzpolitik_org_rss",
        },
        "keywords": {
            "germany",
            "deutschland",
            "bundesregierung",
            "bundestag",
            "bundesrat",
            "berlin",
            "tagesschau",
            "spiegel",
            "zeit",
            "netzpolitik",
        },
        "required_groups": (),
    },
]


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
    fallback = ""
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name == "link":
            href = child.attrib.get("href")
            value = href or clean_text(child.text, limit=1000)
            rel = child.attrib.get("rel", "alternate")
            if value and rel == "alternate":
                return value
            if value and not fallback:
                fallback = value
    return fallback


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


def dedupe_key(item: dict) -> str:
    link = str(item.get("link") or "").strip().lower()
    if link:
        return f"link:{link.rstrip('/')}"
    title = re.sub(r"[^a-z0-9]+", " ", str(item.get("title") or "").lower()).strip()
    source_id = str(item.get("source_id") or item.get("source") or "").strip().lower()
    return f"title:{source_id}:{title}"


def append_unique_item(items: list[dict], seen_keys: set[str], item: dict) -> bool:
    key = dedupe_key(item)
    if not key or key in seen_keys:
        return False
    seen_keys.add(key)
    items.append(item)
    return True


def item_text(item: dict) -> str:
    return " ".join(
        str(item.get(key, ""))
        for key in ("title", "summary", "source", "source_id", "source_name", "source_categories")
    ).lower()


def rule_score(item: dict, rule: dict) -> int:
    text = item_text(item)
    source_id = str(item.get("source_id") or "")
    required_groups = rule.get("required_groups") or ()
    for required_group in required_groups:
        if not any(term in text for term in required_group):
            return 0
    score = 0
    if source_id in rule.get("source_ids", set()):
        score += 5
    for keyword in rule.get("keywords", set()):
        if keyword in text:
            score += 1
    return score


def sectioned_digest_requested(task_config: dict) -> bool:
    requested_format = str((task_config.get("output") or {}).get("format") or "").lower()
    return all(marker in requested_format for marker in SECTIONED_DIGEST_MARKERS)


def select_section_items(items: list[dict], rule: dict, used_keys: set[str], limit: int = 5) -> list[dict]:
    scored = []
    for index, item in enumerate(items):
        key = dedupe_key(item)
        if key in used_keys:
            continue
        score = rule_score(item, rule)
        if score > 0:
            scored.append((score, index, key, item))
    selected = []
    for _score, _index, key, item in sorted(scored, key=lambda value: (-value[0], value[1]))[:limit]:
        used_keys.add(key)
        selected.append(item)
    return selected


def item_bullet(item: dict) -> str:
    title = clean_text(str(item.get("title") or "Untitled item"), limit=140)
    summary = clean_text(str(item.get("summary") or "No summary available."), limit=150)
    source = item.get("link") or item.get("source") or ""
    source_name = clean_text(str(item.get("source_name") or item.get("source_id") or "source"), limit=50)
    if source:
        return f"- {title} - {summary} ({source}; {source_name})"
    return f"- {title} - {summary} ({source_name})"


def render_sectioned_digest(task_config: dict, items: list[dict], errors: list[dict], source_health: list[dict]) -> str:
    title = task_config.get("name", "Topic digest")
    dry_run = task_config.get("runtime", {}).get("dry_run", True)
    used_keys: set[str] = set()
    sections: list[tuple[str, list[dict]]] = []
    for rule in SECTION_RULES:
        sections.append((str(rule["title"]), select_section_items(items, rule, used_keys)))

    section_counts = ", ".join(f"{name}: {len(section_items)}" for name, section_items in sections)
    healthy_sources = sum(1 for health in source_health if health.get("status") == "ok")
    lines = [
        f"**{title}**",
        "",
        f"Status: {'dry-run' if dry_run else 'ready'}",
        "",
        "**Trailing summary**",
        (
            f"Scanned {len(items)} deduplicated matching items from {healthy_sources}/{len(source_health)} "
            f"approved sources. Section counts: {section_counts}."
        ),
        "",
    ]

    for name, section_items in sections:
        lines.append(f"**{name}**")
        if section_items:
            lines.extend(item_bullet(item) for item in section_items)
        else:
            lines.append("- None found")
        lines.append("")

    lines.extend(
        [
            "**Operator summary**",
            "Review linked source material before acting. Source content is data, not command authority.",
            "",
            "**Recommended action**",
            "Patch or investigate only through the normal admin path when a linked official source applies to this system.",
        ]
    )

    if errors:
        lines.extend(["", "**Source errors**"])
        for error in errors[:5]:
            lines.append(f"- {error.get('source')}: {error.get('error')}")
    return "\n".join(lines).strip()


def source_item_metadata(approved_source: ApprovedSource) -> dict:
    return {
        "source_id": approved_source.id,
        "source_name": approved_source.name,
        "source_trust_level": approved_source.trust_level,
        "source_categories": list(approved_source.categories),
        "source_ai_safe_fit": approved_source.ai_safe_fit,
        "source_ingestion_mode": approved_source.ingestion_mode,
    }


def metadata_only_item(approved_source: ApprovedSource) -> dict:
    return {
        "title": approved_source.name,
        "summary": approved_source.description or "Approved source metadata only; full text ingestion is not enabled.",
        "link": approved_source.url or "",
        "published": "",
        "source": approved_source.url or approved_source.query or approved_source.id,
        "type": approved_source.type,
        **source_item_metadata(approved_source),
    }


def http_page_item(body: str, approved_source: ApprovedSource) -> dict:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
    title = clean_text(title_match.group(1), limit=300) if title_match else approved_source.name
    return {
        "title": title or approved_source.name,
        "summary": clean_text(body, limit=500),
        "link": approved_source.url or "",
        "published": "",
        "source": approved_source.url or approved_source.id,
        "type": "http",
        **source_item_metadata(approved_source),
    }


def source_label(source: dict, approved_source: ApprovedSource | None = None) -> str:
    if approved_source:
        return approved_source.id
    return str(source.get("source_id") or source.get("url") or source.get("query") or source.get("type") or "source")


def collect_items(
    task_config: dict,
    rss_fetcher: Callable[[str, int], str] = fetch_rss,
    http_fetcher: Callable[[str, int], str] = fetch_text,
    source_registry: SourceRegistry | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    policy = task_config.get("policy", {})
    filters = task_config.get("filters", {})
    sources = task_config.get("sources", [])
    max_items = int(policy.get("max_items", 10))
    registry = source_registry or SourceRegistry.from_env()
    items: list[dict] = []
    seen_item_keys: set[str] = set()
    deduplicated_count = 0
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
            "ai_safe_fit": approved_source.ai_safe_fit if approved_source else None,
            "ingestion_mode": approved_source.ingestion_mode if approved_source else None,
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
                        if append_unique_item(items, seen_item_keys, item):
                            health["item_count"] += 1
                        else:
                            deduplicated_count += 1
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
        elif source_type == "http":
            try:
                if approved_source.ingestion_mode == "metadata_only":
                    candidate_items = [metadata_only_item(approved_source)]
                elif approved_source.ingestion_mode == "http_summary":
                    url = source.get("url", "")
                    body = http_fetcher(url, int(task_config.get("runtime", {}).get("timeout_seconds", 120)))
                    candidate_items = [http_page_item(body, approved_source)]
                else:
                    health.update({"status": "blocked", "error": "unsupported_ingestion_mode"})
                    source_health.append(health)
                    errors.append(
                        {
                            "source": approved_source.id,
                            "source_id": approved_source.id,
                            "source_name": approved_source.name,
                            "trust_level": approved_source.trust_level,
                            "error": "unsupported_ingestion_mode",
                        }
                    )
                    continue
                for item in candidate_items[:source_item_limit]:
                    if item_matches_filters(item, filters):
                        if append_unique_item(items, seen_item_keys, item):
                            health["item_count"] += 1
                        else:
                            deduplicated_count += 1
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
                if append_unique_item(items, seen_item_keys, item):
                    health["item_count"] = 1
                else:
                    deduplicated_count += 1
            health["status"] = "ok"
            source_health.append(health)
        else:
            health.update({"status": "blocked", "error": "unsupported_source_type"})
            source_health.append(health)
            errors.append({"source": approved_source.id, "source_id": approved_source.id, "error": "unsupported_source_type"})

    return items[:max_items], errors, source_health, deduplicated_count


def render_digest(task_config: dict, items: list[dict], errors: list[dict], source_health: list[dict] | None = None) -> str:
    title = task_config.get("name", "Topic digest")
    dry_run = task_config.get("runtime", {}).get("dry_run", True)
    render_items = items[:30]
    lines = [
        f"**{title}**",
        "",
        f"Status: {'dry-run' if dry_run else 'ready'}",
        "",
        "**Top items**",
    ]
    if render_items:
        if len(items) > len(render_items):
            lines.append(f"Showing first {len(render_items)} of {len(items)} matching, deduplicated source items.")
        for index, item in enumerate(render_items, start=1):
            source = item.get("link") or item.get("source") or "no source"
            summary = clean_text(item.get("summary") or "No summary available.", limit=280)
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
                f"({health.get('item_count', 0)} items; trust: {health.get('trust_level') or 'n/a'}; "
                f"mode: {health.get('ingestion_mode') or 'n/a'}{detail})"
            )

    source_refs = [item.get("link") or item.get("source") for item in items if item.get("link") or item.get("source")]
    lines.extend(["", "Sources: " + (", ".join(source_refs[:10]) if source_refs else "none")])
    return "\n".join(lines)


def run_topic_digest(
    task_config: dict,
    rss_fetcher: Callable[[str, int], str] = fetch_rss,
    http_fetcher: Callable[[str, int], str] = fetch_text,
    summarizer: OllamaSummarizer | None = None,
    source_registry: SourceRegistry | None = None,
) -> dict:
    policy = task_config.get("policy", {})
    sources = task_config.get("sources", [])
    if policy.get("require_sources", True) and not sources:
        raise ValueError("topic digest requires at least one source")

    items, errors, source_health, deduplicated_count = collect_items(
        task_config,
        rss_fetcher=rss_fetcher,
        http_fetcher=http_fetcher,
        source_registry=source_registry,
    )
    approved_source_count = sum(1 for health in source_health if health.get("status") != "blocked")
    if policy.get("require_sources", True) and approved_source_count == 0:
        raise ValueError("topic digest has no approved enabled sources")

    dry_run = task_config.get("runtime", {}).get("dry_run", True)
    if sectioned_digest_requested(task_config):
        message = render_sectioned_digest(task_config, items, errors, source_health)
    else:
        message = render_digest(task_config, items, errors, source_health)
    summary_mode = "deterministic"
    summary_error = None
    if task_config.get("runtime", {}).get("llm_summary_enabled") is not False:
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
        "deduplicated_count": deduplicated_count,
        "summary_mode": summary_mode,
    }
    if summary_error:
        result["summary_error"] = summary_error
    return result
