#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from registry_lib import (
    ROOT,
    api_key_from_env,
    diff_registry,
    ensure_import_paths,
    fetch_live_tasks,
    fetch_live_topics,
    format_difference_report,
    load_local_env,
    load_yaml_registry,
    task_config_from_live,
    topic_config_from_live,
)


def diff_live_registry(
    *,
    base_url: str,
    api_key: str,
    config_dir: Path,
) -> list:
    ensure_import_paths()
    live_tasks = {
        task_id: task_config_from_live(task)
        for task_id, task in fetch_live_tasks(base_url, api_key).items()
    }
    live_topics = {
        topic_id: topic_config_from_live(topic)
        for topic_id, topic in fetch_live_topics(base_url, api_key).items()
    }
    return diff_registry(
        local_tasks=load_yaml_registry(config_dir / "tasks"),
        live_tasks=live_tasks,
        local_topics=load_yaml_registry(config_dir / "topics"),
        live_topics=live_topics,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Git YAML task/topic registry with live automation API state.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    parser.add_argument("--api-key-env", default="AUTOMATION_ADMIN_API_KEY")
    parser.add_argument("--json", action="store_true", help="Print machine-readable drift output")
    parser.add_argument("--no-fail-on-drift", action="store_true", help="Return 0 even when drift is detected")
    args = parser.parse_args()

    load_local_env()
    try:
        differences = diff_live_registry(
            base_url=args.base_url,
            api_key=api_key_from_env(args.api_key_env),
            config_dir=args.config_dir,
        )
    except Exception as exc:
        print(f"Diff failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([item.to_dict() for item in differences], indent=2, default=str))
    else:
        print(format_difference_report(differences))
    if differences and not args.no_fail_on_drift:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
