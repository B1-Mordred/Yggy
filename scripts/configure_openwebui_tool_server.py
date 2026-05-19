#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path


DEFAULT_ALLOWED_FUNCTIONS = [
    "health_health_get",
    "list_tasks_tasks_get",
    "get_task_tasks__task_id__get",
    "create_draft_task_tasks_draft_post",
    "list_task_templates_task_templates_get",
    "get_task_template_task_templates__template_id__get",
    "draft_task_from_template_task_templates__template_id__draft_post",
    "propose_task_change_tasks__task_id__propose_change_post",
    "list_task_change_proposals_task_change_proposals_get",
    "get_task_change_proposal_task_change_proposals__proposal_id__get",
    "request_approval_tasks__task_id__request_approval_post",
    "pause_task_tasks__task_id__pause_post",
    "run_task_tasks__task_id__run_post",
    "list_topics_topics_get",
    "draft_topic_topics_draft_post",
    "list_runs_runs_get",
    "get_run_runs__run_id__get",
]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value
    values.update({key: value for key, value in os.environ.items() if key.startswith("AUTOMATION_")})
    return values


def load_json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure Open WebUI to use the Yggy automation API tool server.")
    parser.add_argument("--db", default="/var/lib/docker/volumes/open-webui/_data/webui.db")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model-id", default="webui")
    parser.add_argument("--server-id", default="yggy_automation_api")
    parser.add_argument("--server-url", default="http://automation-api:8088")
    parser.add_argument("--openapi-path", default="/openapi.json")
    parser.add_argument("--system-prompt", default="yggdrasil/system_prompt.md")
    parser.add_argument("--user-id", default="")
    args = parser.parse_args()

    db_path = Path(args.db)
    env_values = read_env(Path(args.env_file))
    tool_key = env_values.get("AUTOMATION_TOOL_API_KEY", "").strip()
    if not tool_key:
        raise SystemExit("AUTOMATION_TOOL_API_KEY is required")
    if not db_path.exists():
        raise SystemExit(f"Open WebUI database not found: {db_path}")

    backup = db_path.with_name(f"{db_path.name}.bak.automation-tool-{time.strftime('%Y%m%d%H%M%S')}")
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)

    prompt_path = Path(args.system_prompt)
    system_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute("select id, data from config order by id desc limit 1").fetchone()
        if row is None:
            config_id = 1
            config = {"version": 0, "ui": {}}
            cur.execute("insert into config (id, data, version) values (?, ?, ?)", (config_id, json.dumps(config), 0))
        else:
            config_id = row["id"]
            config = json.loads(row["data"])

        user_id = args.user_id.strip()
        if not user_id:
            user = cur.execute("select id from user where role = 'admin' order by created_at asc limit 1").fetchone()
            user_id = user["id"] if user else "*"

        tool_server = {
            "url": args.server_url.rstrip("/"),
            "path": args.openapi_path,
            "type": "openapi",
            "auth_type": "none",
            "headers": {"X-Automation-Api-Key": tool_key},
            "key": "",
            "config": {
                "enable": True,
                "function_name_filter_list": ",".join(DEFAULT_ALLOWED_FUNCTIONS),
                "access_grants": [{"principal_type": "user", "principal_id": user_id, "permission": "read"}],
            },
            "info": {
                "id": args.server_id,
                "name": "Yggy Automation Control Plane",
                "description": "Policy-enforced automation API. Drafts tasks, requests approvals, runs approved low-risk tasks, and reads audit/run state.",
            },
        }

        config.setdefault("tool_server", {})
        connections = config["tool_server"].setdefault("connections", [])
        connections = [item for item in connections if (item.get("info") or {}).get("id") != args.server_id]
        connections.append(tool_server)
        config["tool_server"]["connections"] = connections
        cur.execute("update config set data = ?, updated_at = current_timestamp where id = ?", (json.dumps(config), config_id))

        model = cur.execute("select meta, params from model where id = ?", (args.model_id,)).fetchone()
        if model:
            meta = load_json(model["meta"], {})
            params = load_json(model["params"], {})
            tool_ids = list(dict.fromkeys([*(meta.get("toolIds") or []), f"server:{args.server_id}"]))
            meta["toolIds"] = tool_ids
            meta.setdefault("capabilities", {})
            meta["capabilities"]["builtin_tools"] = True
            if system_prompt:
                params["system"] = system_prompt
            cur.execute(
                "update model set meta = ?, params = ?, updated_at = ? where id = ?",
                (json.dumps(meta), json.dumps(params), int(time.time()), args.model_id),
            )
        conn.commit()

    print(f"BACKUP={backup}")
    print(f"TOOL_SERVER_ID={args.server_id}")
    print(f"MODEL_ID={args.model_id}")
    print("CONFIGURED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
