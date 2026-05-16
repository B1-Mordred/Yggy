from __future__ import annotations


def run_topic_digest(task_config: dict) -> dict:
    policy = task_config.get("policy", {})
    sources = task_config.get("sources", [])
    if policy.get("require_sources", True) and not sources:
        raise ValueError("topic digest requires at least one source")

    max_items = int(policy.get("max_items", 10))
    items = []
    for source in sources[:max_items]:
        if source.get("type") == "rss":
            items.append({"title": "RSS source queued for digest", "source": source.get("url")})
        elif source.get("type") == "web_query":
            items.append({"title": "Web query queued for digest", "source": source.get("query")})
        else:
            items.append({"title": "Source queued for digest", "source": source.get("type")})

    return {
        "status": "dry_run" if task_config.get("runtime", {}).get("dry_run", True) else "ready",
        "title": task_config.get("name", "Topic digest"),
        "items": items,
        "source_count": len(sources),
    }
