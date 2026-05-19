from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistryDifference:
    resource_type: str
    resource_id: str
    kind: str
    path: str | None = None
    local: Any = None
    live: Any = None
    risk: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


RISKY_TASK_PATHS = {
    "$.enabled": "enablement",
    "$.trigger.cron": "schedule",
    "$.trigger.timezone": "schedule",
    "$.output.channel": "output",
    "$.output.target": "output",
    "$.policy.approval_level": "approval",
    "$.policy.allow_external_side_effects": "side_effect",
    "$.policy.allow_filesystem_write": "filesystem",
    "$.policy.allow_shell": "forbidden_capability",
    "$.policy.allow_docker_socket": "forbidden_capability",
    "$.runtime.dry_run": "runtime_mode",
}


def load_local_env(root: Path = ROOT) -> None:
    env_path = root / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def api_key_from_env(env_name: str = "AUTOMATION_ADMIN_API_KEY") -> str:
    key = os.getenv(env_name, "").strip()
    if not key:
        raise RegistryError(f"{env_name} is required in the local environment or .env")
    return key


def api_request(
    method: str,
    path: str,
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 15,
) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    with httpx.Client(timeout=timeout) as client:
        response = client.request(
            method,
            url,
            headers={"X-Automation-Api-Key": api_key},
            json=payload,
        )
    if response.status_code >= 400:
        raise RegistryError(f"{method} {path} returned {response.status_code}: {response.text}")
    if not response.content:
        return {}
    return response.json()


def fetch_live_tasks(base_url: str, api_key: str) -> dict[str, dict[str, Any]]:
    tasks = api_request("GET", "/tasks", base_url=base_url, api_key=api_key)
    if not isinstance(tasks, list):
        raise RegistryError("GET /tasks did not return a list")
    return {str(task["id"]): task for task in tasks}


def fetch_live_topics(base_url: str, api_key: str) -> dict[str, dict[str, Any]]:
    topics = api_request("GET", "/topics", base_url=base_url, api_key=api_key)
    if not isinstance(topics, list):
        raise RegistryError("GET /topics did not return a list")
    return {str(topic["id"]): topic for topic in topics}


def task_config_from_live(task: dict[str, Any]) -> dict[str, Any]:
    config = dict(task.get("config") or {})
    if task.get("enabled") is not None:
        config["enabled"] = bool(task["enabled"])
    return config


def topic_config_from_live(topic: dict[str, Any]) -> dict[str, Any]:
    config = dict(topic.get("config") or {})
    if topic.get("enabled") is not None:
        config["enabled"] = bool(topic["enabled"])
    return config


def load_yaml_file(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RegistryError(f"{path} did not contain a YAML mapping")
    return data


def dump_yaml_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")


def load_yaml_registry(directory: Path) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    if not directory.exists():
        return registry
    for path in sorted(directory.glob("*.yaml")):
        data = load_yaml_file(path)
        resource_id = data.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            raise RegistryError(f"{path} is missing id")
        if resource_id in registry:
            raise RegistryError(f"duplicate id in {directory}: {resource_id}")
        registry[resource_id] = data
    return registry


def normalize_task_config(data: dict[str, Any]) -> dict[str, Any]:
    from app.schemas import TaskConfig

    return TaskConfig.model_validate(data).model_dump(mode="json")


def normalize_topic_config(data: dict[str, Any]) -> dict[str, Any]:
    from app.schemas import TopicConfig

    return TopicConfig.model_validate(data).model_dump(mode="json")


def validate_task_against_policy(data: dict[str, Any]) -> dict[str, Any]:
    from app.policy import load_policy, validate_task_policy
    from app.schemas import TaskConfig

    task = TaskConfig.model_validate(data)
    validate_task_policy(task, load_policy(str(ROOT / "configs" / "policies.yaml")))
    return task.model_dump(mode="json")


def validate_topic_config(data: dict[str, Any]) -> dict[str, Any]:
    from app.schemas import TopicConfig

    return TopicConfig.model_validate(data).model_dump(mode="json")


def recursive_differences(
    local: Any,
    live: Any,
    *,
    path: str = "$",
    resource_type: str,
    resource_id: str,
) -> list[RegistryDifference]:
    if isinstance(local, dict) and isinstance(live, dict):
        differences: list[RegistryDifference] = []
        for key in sorted(set(local) | set(live)):
            child_path = f"{path}.{key}"
            if key not in local:
                differences.append(
                    RegistryDifference(resource_type, resource_id, "field_missing_local", child_path, None, live[key], risk_for_path(child_path))
                )
            elif key not in live:
                differences.append(
                    RegistryDifference(resource_type, resource_id, "field_missing_live", child_path, local[key], None, risk_for_path(child_path))
                )
            else:
                differences.extend(
                    recursive_differences(local[key], live[key], path=child_path, resource_type=resource_type, resource_id=resource_id)
                )
        return differences
    if isinstance(local, list) and isinstance(live, list):
        if canonical_json(local) == canonical_json(live):
            return []
        return [RegistryDifference(resource_type, resource_id, "field_changed", path, local, live, risk_for_path(path))]
    if local != live:
        return [RegistryDifference(resource_type, resource_id, "field_changed", path, local, live, risk_for_path(path))]
    return []


def risk_for_path(path: str) -> str:
    return RISKY_TASK_PATHS.get(path, "normal")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def diff_registry(
    *,
    local_tasks: dict[str, dict[str, Any]],
    live_tasks: dict[str, dict[str, Any]],
    local_topics: dict[str, dict[str, Any]] | None = None,
    live_topics: dict[str, dict[str, Any]] | None = None,
) -> list[RegistryDifference]:
    differences: list[RegistryDifference] = []
    differences.extend(diff_resource_configs("task", local_tasks, live_tasks, normalize_task_config))
    differences.extend(diff_resource_configs("topic", local_topics or {}, live_topics or {}, normalize_topic_config))
    return differences


def diff_resource_configs(
    resource_type: str,
    local_configs: dict[str, dict[str, Any]],
    live_configs: dict[str, dict[str, Any]],
    normalize,
) -> list[RegistryDifference]:
    differences: list[RegistryDifference] = []
    for resource_id in sorted(set(local_configs) | set(live_configs)):
        if resource_id not in live_configs:
            differences.append(RegistryDifference(resource_type, resource_id, "missing_live", "$", local_configs[resource_id], None))
            continue
        if resource_id not in local_configs:
            differences.append(RegistryDifference(resource_type, resource_id, "missing_yaml", "$", None, live_configs[resource_id]))
            continue
        local = normalize(local_configs[resource_id])
        live = normalize(live_configs[resource_id])
        differences.extend(
            recursive_differences(local, live, resource_type=resource_type, resource_id=resource_id)
        )
    return differences


def summarized_difference_counts(differences: list[RegistryDifference]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for difference in differences:
        key = f"{difference.resource_type}.{difference.kind}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def format_difference_report(differences: list[RegistryDifference]) -> str:
    if not differences:
        return "Registry drift: none detected."
    lines = ["Registry drift detected:"]
    for difference in differences:
        detail = f"- {difference.resource_type} `{difference.resource_id}` {difference.kind}"
        if difference.path:
            detail += f" at `{difference.path}`"
        if difference.risk != "normal":
            detail += f" risk `{difference.risk}`"
        if difference.kind == "field_changed":
            detail += f": local `{short_value(difference.local)}` live `{short_value(difference.live)}`"
        lines.append(detail)
    lines.append("")
    lines.append("Summary:")
    for key, count in summarized_difference_counts(differences).items():
        lines.append(f"- {key}: {count}")
    return "\n".join(lines)


def short_value(value: Any, limit: int = 160) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    if len(text) > limit:
        return f"{text[:limit]}...<truncated>"
    return text


def clean_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def ensure_import_paths() -> None:
    import sys

    api_path = str(ROOT / "automation-api")
    if api_path not in sys.path:
        sys.path.insert(0, api_path)
