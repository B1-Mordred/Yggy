#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from registry_lib import (
    ROOT,
    api_key_from_env,
    clean_directory,
    dump_yaml_file,
    ensure_import_paths,
    fetch_live_tasks,
    fetch_live_topics,
    load_local_env,
    task_config_from_live,
    topic_config_from_live,
    validate_task_against_policy,
    validate_topic_config,
)


def export_live_configs(
    *,
    base_url: str,
    out_dir: Path,
    api_key: str,
    clean: bool = False,
) -> dict:
    ensure_import_paths()
    if clean:
        clean_directory(out_dir)
    tasks_dir = out_dir / "tasks"
    topics_dir = out_dir / "topics"
    tasks = fetch_live_tasks(base_url, api_key)
    topics = fetch_live_topics(base_url, api_key)

    exported_tasks: list[str] = []
    exported_topics: list[str] = []
    for task_id, task in sorted(tasks.items()):
        config = validate_task_against_policy(task_config_from_live(task))
        dump_yaml_file(tasks_dir / f"{task_id}.yaml", config)
        exported_tasks.append(task_id)
    for topic_id, topic in sorted(topics.items()):
        config = validate_topic_config(topic_config_from_live(topic))
        dump_yaml_file(topics_dir / f"{topic_id}.yaml", config)
        exported_topics.append(topic_id)

    manifest = {
        "base_url": base_url.rstrip("/"),
        "task_count": len(exported_tasks),
        "topic_count": len(exported_topics),
        "tasks": exported_tasks,
        "topics": exported_topics,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export live automation API task/topic configs to generated YAML snapshots.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "exports" / "live")
    parser.add_argument("--api-key-env", default="AUTOMATION_ADMIN_API_KEY")
    parser.add_argument("--clean", action="store_true", help="Remove the output directory before exporting")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary")
    args = parser.parse_args()

    load_local_env()
    try:
        manifest = export_live_configs(
            base_url=args.base_url,
            out_dir=args.out_dir,
            api_key=api_key_from_env(args.api_key_env),
            clean=args.clean,
        )
    except Exception as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(manifest, indent=2))
    else:
        print(
            f"Exported {manifest['task_count']} tasks and {manifest['topic_count']} topics to {args.out_dir}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
