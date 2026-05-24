#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if __name__ == "__main__" and sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

from registry_lib import RegistryError, api_key_from_env, api_request, load_local_env


DEFAULT_LOCK_PATH = "/tmp/yggy-capability-implementation-runner.lock"
DEFAULT_IMPLEMENTATION_SCRIPT = ROOT / "scripts" / "implement_capability_plan.py"
DEFAULT_DEPLOY_SERVICES = (
    "automation-api",
    "automation-worker",
    "bragi",
    "channel-bridge",
    "metrics-exporter",
    "printer-status-exporter",
)
MAX_DEPLOY_SNIPPET_LENGTH = 1600


@dataclass(frozen=True)
class RunnerConfig:
    base_url: str
    api_key_env: str
    env_root: Path
    source_root: Path
    repo_root: Path
    managed_workspace: Path | None
    python: Path
    implementation_script: Path
    poll_seconds: float
    batch_size: int
    once: bool
    dry_run: bool
    lock_path: Path
    command_timeout: int
    implementation_timeout: int
    staged: bool
    fresh_profile: bool
    allow_dirty: bool
    no_yolo: bool
    mark_failed_on_wrapper_error: bool
    extra_args: tuple[str, ...]
    created_after: str
    manual_only: bool
    manual_override: bool
    quiet_hours_start: str
    quiet_hours_end: str
    quiet_hours_timezone: str
    implementation_ollama_host: str
    implementation_model: str
    stop_model_after_run: bool
    deploy_enabled: bool
    deploy_root: Path
    deploy_services: tuple[str, ...]
    deploy_timeout: int
    deploy_dry_run: bool
    deploy_require_clean_index: bool


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Poll queued Yggy capability implementation runs and invoke the bounded "
            "host-side implementation harness."
        )
    )
    parser.add_argument("--base-url", default=os.getenv("AUTOMATION_API_BASE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--api-key-env", default=os.getenv("YGGY_IMPLEMENTATION_RUNNER_API_KEY_ENV", "AUTOMATION_ADMIN_API_KEY"))
    parser.add_argument("--env-root", type=Path, default=Path(os.getenv("YGGY_IMPLEMENTATION_ENV_ROOT", str(ROOT))))
    parser.add_argument("--source-root", type=Path, default=Path(os.getenv("YGGY_IMPLEMENTATION_RUNNER_SOURCE_ROOT", str(ROOT))))
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(os.getenv("YGGY_IMPLEMENTATION_REPO_ROOT", str(ROOT))),
        help="Repository to pass to the implementation harness when --managed-workspace is not set.",
    )
    parser.add_argument(
        "--managed-workspace",
        type=Path,
        default=Path(os.getenv("YGGY_IMPLEMENTATION_RUNNER_WORKSPACE"))
        if os.getenv("YGGY_IMPLEMENTATION_RUNNER_WORKSPACE")
        else None,
        help=(
            "Optional clean workspace managed by this runner. It is reset to the source "
            "repo HEAD before every run and must not contain secrets."
        ),
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(os.getenv("YGGY_IMPLEMENTATION_RUNNER_PYTHON", str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable))),
    )
    parser.add_argument(
        "--implementation-script",
        type=Path,
        default=Path(os.getenv("YGGY_IMPLEMENTATION_RUNNER_SCRIPT", str(DEFAULT_IMPLEMENTATION_SCRIPT))),
    )
    parser.add_argument("--poll-seconds", type=float, default=float(os.getenv("YGGY_IMPLEMENTATION_RUNNER_POLL_SECONDS", "30")))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("YGGY_IMPLEMENTATION_RUNNER_BATCH_SIZE", "1")))
    parser.add_argument("--once", action="store_true", default=env_bool("YGGY_IMPLEMENTATION_RUNNER_ONCE", False))
    parser.add_argument("--dry-run", action="store_true", default=env_bool("YGGY_IMPLEMENTATION_RUNNER_DRY_RUN", False))
    parser.add_argument("--lock-path", type=Path, default=Path(os.getenv("YGGY_IMPLEMENTATION_RUNNER_LOCK_PATH", DEFAULT_LOCK_PATH)))
    parser.add_argument(
        "--command-timeout",
        type=int,
        default=int(os.getenv("YGGY_IMPLEMENTATION_RUNNER_COMMAND_TIMEOUT", "0")),
        help="Whole implementation command timeout in seconds. 0 means no wrapper-level timeout.",
    )
    parser.add_argument(
        "--implementation-timeout",
        type=int,
        default=int(os.getenv("YGGY_IMPLEMENTATION_RUNNER_IMPLEMENTATION_TIMEOUT", "1800")),
        help="Timeout passed to scripts/implement_capability_plan.py for each Hermes subprocess.",
    )
    parser.add_argument(
        "--staged",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_STAGED", True),
    )
    parser.add_argument(
        "--fresh-profile",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_FRESH_PROFILE", True),
    )
    parser.add_argument(
        "--allow-dirty",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_ALLOW_DIRTY", False),
    )
    parser.add_argument(
        "--no-yolo",
        action="store_true",
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_NO_YOLO", False),
    )
    parser.add_argument(
        "--mark-failed-on-wrapper-error",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_MARK_FAILED_ON_WRAPPER_ERROR", True),
    )
    parser.add_argument(
        "--extra-args",
        default=os.getenv("YGGY_IMPLEMENTATION_RUNNER_EXTRA_ARGS", ""),
        help="Extra args appended to scripts/implement_capability_plan.py. Parsed with shlex.",
    )
    parser.add_argument(
        "--created-after",
        default=os.getenv("YGGY_IMPLEMENTATION_RUNNER_CREATED_AFTER", ""),
        help="Optional ISO timestamp/string gate; queued runs older than this are ignored.",
    )
    parser.add_argument(
        "--manual-only",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_MANUAL_ONLY", False),
        help="Do not automatically process queued runs unless --manual-override is set.",
    )
    parser.add_argument(
        "--manual-override",
        action="store_true",
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_MANUAL_OVERRIDE", False),
        help="Allow a one-shot/manual runner invocation to process runs despite manual-only or quiet hours.",
    )
    parser.add_argument(
        "--quiet-hours-start",
        default=os.getenv("YGGY_IMPLEMENTATION_RUNNER_QUIET_HOURS_START", ""),
        help="Optional local quiet-hours start in HH:MM. Queued runs wait unless --manual-override is set.",
    )
    parser.add_argument(
        "--quiet-hours-end",
        default=os.getenv("YGGY_IMPLEMENTATION_RUNNER_QUIET_HOURS_END", ""),
        help="Optional local quiet-hours end in HH:MM.",
    )
    parser.add_argument(
        "--quiet-hours-timezone",
        default=os.getenv("YGGY_IMPLEMENTATION_RUNNER_QUIET_HOURS_TIMEZONE", os.getenv("TZ", "Europe/Berlin")),
    )
    parser.add_argument(
        "--implementation-ollama-host",
        default=os.getenv("YGGY_IMPLEMENTATION_OLLAMA_HOST", ""),
        help="Optional dedicated Ollama host passed to Hermes implementation subprocesses.",
    )
    parser.add_argument(
        "--implementation-model",
        default=os.getenv("YGGY_IMPLEMENTATION_HERMES_MODEL", ""),
        help="Implementation model name used for post-run Ollama cleanup.",
    )
    parser.add_argument(
        "--stop-model-after-run",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_STOP_MODEL_AFTER_RUN", True),
        help="Stop the implementation model after each runner subprocess when ollama is available.",
    )
    parser.add_argument(
        "--deploy-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_DEPLOY_ENABLED", False),
        help="Also process deploy-approved implementation runs with the fixed host-side deployment path.",
    )
    parser.add_argument(
        "--deploy-root",
        type=Path,
        default=Path(os.getenv("YGGY_IMPLEMENTATION_RUNNER_DEPLOY_ROOT", str(ROOT))),
        help="Production checkout where reviewed implementation commits are applied and deployed.",
    )
    parser.add_argument(
        "--deploy-services",
        default=os.getenv("YGGY_IMPLEMENTATION_RUNNER_DEPLOY_SERVICES", ",".join(DEFAULT_DEPLOY_SERVICES)),
        help="Comma-separated Docker Compose services the fixed deployment command may rebuild/restart.",
    )
    parser.add_argument(
        "--deploy-timeout",
        type=int,
        default=int(os.getenv("YGGY_IMPLEMENTATION_RUNNER_DEPLOY_TIMEOUT", "900")),
        help="Timeout in seconds for each fixed deployment command.",
    )
    parser.add_argument(
        "--deploy-dry-run",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_DEPLOY_DRY_RUN", False),
        help="Log deploy-approved runs without changing git or Docker state.",
    )
    parser.add_argument(
        "--deploy-require-clean-index",
        action=argparse.BooleanOptionalAction,
        default=env_bool("YGGY_IMPLEMENTATION_RUNNER_DEPLOY_REQUIRE_CLEAN_INDEX", True),
        help="Refuse deployment when the production checkout has tracked changes.",
    )
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> RunnerConfig:
    return RunnerConfig(
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        env_root=args.env_root.resolve(),
        source_root=args.source_root.resolve(),
        repo_root=args.repo_root.resolve(),
        managed_workspace=args.managed_workspace.resolve() if args.managed_workspace else None,
        python=args.python.resolve(),
        implementation_script=args.implementation_script.resolve(),
        poll_seconds=max(1.0, args.poll_seconds),
        batch_size=max(1, args.batch_size),
        once=bool(args.once),
        dry_run=bool(args.dry_run),
        lock_path=args.lock_path,
        command_timeout=max(0, args.command_timeout),
        implementation_timeout=max(1, args.implementation_timeout),
        staged=bool(args.staged),
        fresh_profile=bool(args.fresh_profile),
        allow_dirty=bool(args.allow_dirty),
        no_yolo=bool(args.no_yolo),
        mark_failed_on_wrapper_error=bool(args.mark_failed_on_wrapper_error),
        extra_args=tuple(shlex.split(args.extra_args)),
        created_after=str(args.created_after or "").strip(),
        manual_only=bool(args.manual_only),
        manual_override=bool(args.manual_override),
        quiet_hours_start=str(args.quiet_hours_start or "").strip(),
        quiet_hours_end=str(args.quiet_hours_end or "").strip(),
        quiet_hours_timezone=str(args.quiet_hours_timezone or "Europe/Berlin").strip(),
        implementation_ollama_host=str(args.implementation_ollama_host or "").strip(),
        implementation_model=str(args.implementation_model or "").strip(),
        stop_model_after_run=bool(args.stop_model_after_run),
        deploy_enabled=bool(args.deploy_enabled),
        deploy_root=args.deploy_root.resolve(),
        deploy_services=parse_deploy_services(args.deploy_services),
        deploy_timeout=max(1, args.deploy_timeout),
        deploy_dry_run=bool(args.deploy_dry_run),
        deploy_require_clean_index=bool(args.deploy_require_clean_index),
    )


def parse_deploy_services(value: str) -> tuple[str, ...]:
    services = tuple(part.strip() for part in value.split(",") if part.strip())
    invalid = [service for service in services if not re.match(r"^[A-Za-z0-9_.-]+$", service)]
    if invalid:
        raise ValueError(f"invalid deploy service names: {', '.join(invalid)}")
    if not services:
        raise ValueError("at least one deploy service must be configured")
    return services


@contextlib.contextmanager
def runner_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def list_queued_runs(config: RunnerConfig, api_key: str) -> list[dict[str, Any]]:
    runs = api_request(
        "GET",
        f"/capability-implementation-runs?status=queued&limit={config.batch_size}",
        base_url=config.base_url,
        api_key=api_key,
    )
    if not isinstance(runs, list):
        raise RegistryError("GET /capability-implementation-runs did not return a list")
    filtered = [run for run in runs if should_process_run(run, config.created_after)]
    return sorted(filtered, key=lambda run: str(run.get("created_at") or ""))


def should_process_run(run: dict[str, Any], created_after: str) -> bool:
    if not created_after:
        return True
    return str(run.get("created_at") or "") >= created_after


def prepare_repo_root(config: RunnerConfig) -> Path:
    if config.managed_workspace is None:
        return config.repo_root

    workspace = config.managed_workspace
    source_head = git_output(config.source_root, "rev-parse", "HEAD")
    source_branch = git_output(config.source_root, "branch", "--show-current") or "dev"

    if not (workspace / ".git").exists():
        workspace.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--no-local", "--branch", source_branch, str(config.source_root), str(workspace)],
            check=True,
        )
    else:
        subprocess.run(["git", "-C", str(workspace), "fetch", "origin", source_branch], check=True)
        ensure_branch(workspace, source_branch)

    subprocess.run(["git", "-C", str(workspace), "reset", "--hard", source_head], check=True)
    subprocess.run(["git", "-C", str(workspace), "clean", "-fdx", "-e", ".venv"], check=True)
    ensure_workspace_venv(config.source_root, workspace)
    grant_workspace_access(workspace)
    return workspace


def ensure_branch(workspace: Path, branch: str) -> None:
    current = git_output(workspace, "branch", "--show-current")
    if current == branch:
        return
    branches = git_output(workspace, "branch", "--list", branch)
    if branches:
        subprocess.run(["git", "-C", str(workspace), "switch", branch], check=True)
    else:
        subprocess.run(["git", "-C", str(workspace), "switch", "-c", branch, f"origin/{branch}"], check=True)


def ensure_workspace_venv(source_root: Path, workspace: Path) -> None:
    source_venv = source_root / ".venv"
    target_venv = workspace / ".venv"
    exclude_path = workspace / ".git" / "info" / "exclude"
    if exclude_path.exists():
        current = exclude_path.read_text(encoding="utf-8")
        if ".venv\n" not in current and "\n.venv\n" not in current:
            exclude_path.write_text(f"{current.rstrip()}\n.venv\n", encoding="utf-8")
    if target_venv.exists() or not source_venv.exists():
        return
    target_venv.symlink_to(source_venv, target_is_directory=True)


def grant_workspace_access(workspace: Path) -> None:
    hermes_user = os.getenv("YGGY_IMPLEMENTATION_HERMES_USER", "").strip()
    if not hermes_user:
        return
    if not shutil.which("setfacl"):
        return
    subprocess.run(["setfacl", "-Rm", f"u:{hermes_user}:rwX", str(workspace)], check=False)
    subprocess.run(["setfacl", "-Rm", f"d:u:{hermes_user}:rwX", str(workspace)], check=False)


def git_output(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo_root), *args], text=True).strip()


def implementation_command(config: RunnerConfig, run: dict[str, Any], repo_root: Path) -> list[str]:
    command = [
        str(config.python),
        str(config.implementation_script),
        "--run-id",
        str(run["id"]),
        "--base-url",
        config.base_url,
        "--api-key-env",
        config.api_key_env,
        "--env-root",
        str(config.env_root),
        "--repo-root",
        str(repo_root),
        "--timeout",
        str(config.implementation_timeout),
    ]
    if config.staged:
        command.append("--staged")
    if config.fresh_profile:
        command.append("--fresh-profile")
    if config.allow_dirty:
        command.append("--allow-dirty")
    if config.no_yolo:
        command.append("--no-yolo")
    if config.implementation_ollama_host:
        command.extend(["--ollama-host", config.implementation_ollama_host])
    command.extend(config.extra_args)
    return command


def child_environment() -> dict[str, str]:
    allowed: dict[str, str] = {}
    for key in ("HOME", "PATH", "LANG", "LC_ALL", "SHELL", "USER", "LOGNAME", "DOCKER_HOST", "DOCKER_CONTEXT", "XDG_RUNTIME_DIR"):
        value = os.getenv(key)
        if value:
            allowed[key] = value
    for key, value in os.environ.items():
        if key.startswith("YGGY_IMPLEMENTATION_") or key == "AUTOMATION_API_BASE_URL":
            allowed[key] = value
    return allowed


def process_run(config: RunnerConfig, api_key: str, run: dict[str, Any]) -> int:
    repo_root = prepare_repo_root(config)
    command = implementation_command(config, run, repo_root)
    printable = " ".join(shlex.quote(part) for part in command)
    print(f"Starting capability implementation run {run['id']}: {printable}", flush=True)
    if config.dry_run:
        return 0

    try:
        completed = subprocess.run(
            command,
            cwd=config.source_root,
            env=child_environment(),
            timeout=config.command_timeout or None,
        )
    finally:
        stop_implementation_model(config)
    if completed.returncode != 0 and config.mark_failed_on_wrapper_error:
        mark_failed_if_unclaimed(config, api_key, run["id"], completed.returncode)
    return completed.returncode


def stop_implementation_model(config: RunnerConfig) -> None:
    if not config.stop_model_after_run or not config.implementation_model:
        return
    if not shutil.which("ollama"):
        return
    env = os.environ.copy()
    if config.implementation_ollama_host:
        env["OLLAMA_HOST"] = config.implementation_ollama_host
    subprocess.run(
        ["ollama", "stop", config.implementation_model],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def mark_failed_if_unclaimed(config: RunnerConfig, api_key: str, run_id: str, returncode: int) -> None:
    try:
        latest = api_request("GET", f"/capability-implementation-runs/{run_id}", base_url=config.base_url, api_key=api_key)
        if latest.get("status") == "failed":
            return
        if latest.get("status") not in {"queued", "running"}:
            return
        api_request(
            "PATCH",
            f"/capability-implementation-runs/{run_id}",
            base_url=config.base_url,
            api_key=api_key,
            payload={
                "status": "failed",
                "summary": "Capability implementation runner failed while invoking the host-side harness.",
                "error": f"scripts/implement_capability_plan.py exited with status {returncode}",
            },
        )
    except RegistryError as exc:
        print(f"Could not mark implementation run {run_id} failed: {exc}", file=sys.stderr, flush=True)


class DeploymentError(RuntimeError):
    def __init__(self, message: str, *, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence = evidence or {}


def list_deploy_approved_runs(config: RunnerConfig, api_key: str) -> list[dict[str, Any]]:
    runs = api_request(
        "GET",
        f"/capability-implementation-runs?status=deploy_approved&limit={config.batch_size}",
        base_url=config.base_url,
        api_key=api_key,
    )
    if not isinstance(runs, list):
        raise RegistryError("GET /capability-implementation-runs did not return a list")
    filtered = [run for run in runs if should_process_run(run, config.created_after)]
    return sorted(filtered, key=lambda run: str(run.get("created_at") or ""))


def process_deploy_run(config: RunnerConfig, api_key: str, run: dict[str, Any]) -> int:
    run_id = str(run.get("id") or "")
    commit_sha = str(run.get("commit_sha") or "").strip()
    if not run_id or not commit_sha:
        print(f"Skipping malformed deploy-approved run {run_id or '<missing>'}.", file=sys.stderr, flush=True)
        return 1

    print(f"Starting capability deployment run {run_id} for commit {commit_sha[:12]}.", flush=True)
    if config.deploy_dry_run or config.dry_run:
        return 0

    existing_post_deploy = run.get("post_deploy_results") if isinstance(run.get("post_deploy_results"), dict) else {}
    planned_smoke = existing_post_deploy.get("planned") if isinstance(existing_post_deploy.get("planned"), list) else []
    try:
        patch_implementation_run(
            config,
            api_key,
            run_id,
            {
                "status": "deploying",
                "summary": (
                    "Deployment started from the host-side runner after explicit ops approval. "
                    "Only fixed Yggy service deployment commands are allowed."
                ),
                "post_deploy_results": {
                    **existing_post_deploy,
                    "planned": planned_smoke,
                    "executed": False,
                    "deployment": {
                        "status": "running",
                        "services": list(config.deploy_services),
                        "source_commit": commit_sha,
                        "started_at": utc_timestamp(),
                    },
                },
            },
        )
        result = deploy_reviewed_commit(config, run)
        patch_implementation_run(
            config,
            api_key,
            run_id,
            {
                "status": "deployed",
                "summary": "Deployment completed through the fixed host-side Yggy service runner.",
                "post_deploy_results": {
                    **existing_post_deploy,
                    "planned": planned_smoke,
                    "executed": True,
                    "deployment": result,
                },
            },
        )
        return 0
    except DeploymentError as exc:
        mark_deploy_failed(config, api_key, run_id, existing_post_deploy, planned_smoke, str(exc), exc.evidence)
        return 1
    except Exception as exc:  # noqa: BLE001 - host-runner deployment boundary
        mark_deploy_failed(config, api_key, run_id, existing_post_deploy, planned_smoke, str(exc), {})
        return 1


def deploy_reviewed_commit(config: RunnerConfig, run: dict[str, Any]) -> dict[str, Any]:
    deploy_root = config.deploy_root
    commit_sha = str(run["commit_sha"])
    if not (deploy_root / ".git").exists():
        raise DeploymentError(f"deployment root is not a git checkout: {deploy_root}")
    if not (deploy_root / "docker-compose.automation.yml").exists():
        raise DeploymentError(f"deployment root has no docker-compose.automation.yml: {deploy_root}")

    ensure_commit_available(config, commit_sha, str(run.get("branch") or ""))
    changed_paths = commit_changed_paths(deploy_root, commit_sha)
    ensure_deploy_preconditions(config, changed_paths)

    if commit_already_applied(deploy_root, commit_sha):
        deploy_commit = git_output(deploy_root, "rev-parse", "HEAD")
        apply_result = {"mode": "already_applied", "deploy_commit": deploy_commit}
    else:
        cherry_pick_no_commit(deploy_root, commit_sha)
        deploy_commit = commit_deployment_changes(deploy_root, run)
        apply_result = {"mode": "cherry_pick", "deploy_commit": deploy_commit}

    command_results = run_deployment_commands(config)
    return {
        "status": "completed",
        "source_commit": commit_sha,
        "deploy_commit": apply_result["deploy_commit"],
        "apply": apply_result,
        "changed_paths": sorted(changed_paths),
        "services": list(config.deploy_services),
        "commands": command_results,
        "completed_at": utc_timestamp(),
    }


def patch_implementation_run(config: RunnerConfig, api_key: str, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return api_request(
        "PATCH",
        f"/capability-implementation-runs/{run_id}",
        base_url=config.base_url,
        api_key=api_key,
        payload=payload,
    )


def mark_deploy_failed(
    config: RunnerConfig,
    api_key: str,
    run_id: str,
    existing_post_deploy: dict[str, Any],
    planned_smoke: list[Any],
    error: str,
    evidence: dict[str, Any],
) -> None:
    payload = {
        "status": "deploy_failed",
        "summary": "Deployment failed after the ops gate. Production state needs operator review.",
        "error": safe_snippet(error),
        "post_deploy_results": {
            **existing_post_deploy,
            "planned": planned_smoke,
            "executed": False,
            "deployment": {
                "status": "failed",
                "error": safe_snippet(error),
                "evidence": evidence,
                "completed_at": utc_timestamp(),
            },
        },
    }
    try:
        patch_implementation_run(config, api_key, run_id, payload)
    except RegistryError as exc:
        print(f"Could not mark deployment run {run_id} failed: {exc}", file=sys.stderr, flush=True)


def ensure_commit_available(config: RunnerConfig, commit_sha: str, branch: str) -> None:
    if git_object_exists(config.deploy_root, commit_sha):
        return
    candidates = [path for path in (config.managed_workspace, config.source_root) if path and path != config.deploy_root]
    for candidate in candidates:
        if not (candidate / ".git").exists():
            continue
        fetch_commit_from_candidate(config.deploy_root, candidate, branch, commit_sha)
        if git_object_exists(config.deploy_root, commit_sha):
            return
    raise DeploymentError(
        f"implementation commit {commit_sha[:12]} is not available in the deployment checkout or configured workspaces"
    )


def fetch_commit_from_candidate(deploy_root: Path, candidate: Path, branch: str, commit_sha: str) -> None:
    refspecs = []
    if branch:
        refspecs.append(f"{branch}:refs/yggy/deploy-cache/{safe_ref_component(branch)}")
    refspecs.append(f"{commit_sha}:refs/yggy/deploy-cache/{commit_sha[:12]}")
    for refspec in refspecs:
        subprocess.run(
            ["git", "-C", str(deploy_root), "fetch", "--no-tags", str(candidate), refspec],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def git_object_exists(repo_root: Path, commit_sha: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", f"{commit_sha}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def commit_changed_paths(repo_root: Path, commit_sha: str) -> set[str]:
    output = git_output(repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha)
    return {line.strip() for line in output.splitlines() if line.strip()}


def ensure_deploy_preconditions(config: RunnerConfig, changed_paths: set[str]) -> None:
    status_lines = git_status_lines(config.deploy_root)
    tracked_dirty = tracked_dirty_status_lines(status_lines)
    if config.deploy_require_clean_index and tracked_dirty:
        raise DeploymentError(
            "deployment checkout has tracked changes; refusing to mix operator edits with generated capability deployment",
            evidence={"tracked_dirty": tracked_dirty[:20]},
        )
    collisions = untracked_path_collisions(status_lines, changed_paths)
    if collisions:
        raise DeploymentError(
            "deployment checkout has untracked files that collide with the implementation commit",
            evidence={"untracked_collisions": collisions[:20]},
        )
    git_dir = git_output(config.deploy_root, "rev-parse", "--git-dir")
    if (config.deploy_root / git_dir / "CHERRY_PICK_HEAD").exists():
        raise DeploymentError("deployment checkout already has an unfinished cherry-pick")


def git_status_lines(repo_root: Path) -> list[str]:
    output = git_output(repo_root, "status", "--porcelain")
    return [line for line in output.splitlines() if line]


def tracked_dirty_status_lines(status_lines: list[str]) -> list[str]:
    return [line for line in status_lines if not line.startswith("?? ")]


def untracked_path_collisions(status_lines: list[str], changed_paths: set[str]) -> list[str]:
    collisions: list[str] = []
    for line in status_lines:
        if not line.startswith("?? "):
            continue
        path = line[3:].strip()
        if path in changed_paths or any(changed.startswith(f"{path.rstrip('/')}/") for changed in changed_paths):
            collisions.append(path)
    return collisions


def commit_already_applied(repo_root: Path, commit_sha: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", commit_sha, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def cherry_pick_no_commit(repo_root: Path, commit_sha: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "cherry-pick", "--no-commit", commit_sha],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        subprocess.run(["git", "-C", str(repo_root), "cherry-pick", "--abort"], check=False)
        raise DeploymentError(
            f"git cherry-pick failed for {commit_sha[:12]}",
            evidence={"stderr": safe_snippet(completed.stderr), "stdout": safe_snippet(completed.stdout)},
        )


def commit_deployment_changes(repo_root: Path, run: dict[str, Any]) -> str:
    capability_id = str(run.get("capability_id") or "capability")
    run_id = str(run.get("id") or "")
    source_commit = str(run.get("commit_sha") or "")
    message = (
        f"Deploy capability implementation {capability_id}\n\n"
        f"Implementation run: {run_id}\n"
        f"Source commit: {source_commit}\n"
    )
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", message],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeploymentError(
            "git commit failed while recording the deployment commit",
            evidence={"stderr": safe_snippet(completed.stderr), "stdout": safe_snippet(completed.stdout)},
        )
    return git_output(repo_root, "rev-parse", "HEAD")


def run_deployment_commands(config: RunnerConfig) -> list[dict[str, Any]]:
    commands = deployment_commands(config)
    results: list[dict[str, Any]] = []
    for command, capture_stdout in commands:
        results.append(run_fixed_deploy_command(command, config.deploy_root, config.deploy_timeout, capture_stdout=capture_stdout))
    return results


def deployment_commands(config: RunnerConfig) -> list[tuple[list[str], bool]]:
    compose = ["docker", "compose", "-f", "docker-compose.automation.yml"]
    return [
        ([*compose, "config"], False),
        ([*compose, "up", "-d", "--build", *config.deploy_services], True),
        (["curl", "-fsS", "http://127.0.0.1:8088/health"], True),
        ([*compose, "ps"], True),
    ]


def run_fixed_deploy_command(command: list[str], cwd: Path, timeout: int, *, capture_stdout: bool) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=child_environment(),
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": safe_snippet(completed.stdout or "") if capture_stdout else "",
        "stderr": safe_snippet(completed.stderr or ""),
    }
    if completed.returncode != 0:
        raise DeploymentError(f"fixed deploy command failed: {' '.join(command)}", evidence=result)
    return result


def safe_ref_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]+", "-", value).strip("/.")[:120] or "unknown"


def safe_snippet(value: str, *, limit: int = MAX_DEPLOY_SNIPPET_LENGTH) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(password|token|secret|api[_-]?key)(=|:)[^\s]+", r"\1\2[redacted]", text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def process_once(config: RunnerConfig, api_key: str) -> int:
    if should_wait_for_manual_or_quiet_hours(config):
        return 0
    if config.deploy_enabled:
        deploy_runs = list_deploy_approved_runs(config, api_key)
        if deploy_runs:
            failures = 0
            for run in deploy_runs:
                result = process_deploy_run(config, api_key, run)
                if result != 0:
                    failures += 1
            return 1 if failures else 0

    runs = list_queued_runs(config, api_key)
    if not runs:
        suffix = " or deploy-approved runs" if config.deploy_enabled else ""
        print(f"No queued capability implementation runs{suffix}.", flush=True)
        return 0

    failures = 0
    for run in runs:
        result = process_run(config, api_key, run)
        if result != 0:
            failures += 1
    return 1 if failures else 0


def should_wait_for_manual_or_quiet_hours(config: RunnerConfig) -> bool:
    if config.manual_override:
        return False
    if config.manual_only:
        print("Capability implementation runner is in manual-only mode; queued runs will wait.", flush=True)
        return True
    if in_quiet_hours(config):
        print(
            "Capability implementation runner is inside quiet hours "
            f"({config.quiet_hours_start}-{config.quiet_hours_end} {config.quiet_hours_timezone}); queued runs will wait.",
            flush=True,
        )
        return True
    return False


def in_quiet_hours(config: RunnerConfig, *, now: datetime | None = None) -> bool:
    if not config.quiet_hours_start or not config.quiet_hours_end:
        return False
    try:
        start = parse_hhmm(config.quiet_hours_start)
        end = parse_hhmm(config.quiet_hours_end)
        timezone = ZoneInfo(config.quiet_hours_timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        print(f"Ignoring invalid runner quiet-hours configuration: {exc}", file=sys.stderr, flush=True)
        return False
    current = now.astimezone(timezone).time() if now else datetime.now(timezone).time()
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def parse_hhmm(value: str):
    match = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", value.strip())
    if not match:
        raise ValueError(f"invalid HH:MM value: {value}")
    return datetime.strptime(f"{int(match.group(1)):02d}:{match.group(2)}", "%H:%M").time()


def main() -> int:
    args = parse_args()
    config = config_from_args(args)
    load_local_env(config.env_root)
    try:
        api_key = api_key_from_env(config.api_key_env)
    except RegistryError as exc:
        print(exc, file=sys.stderr)
        return 2

    with runner_lock(config.lock_path) as acquired:
        if not acquired:
            print(f"Another capability implementation runner holds {config.lock_path}.", flush=True)
            return 0
        while True:
            try:
                result = process_once(config, api_key)
            except Exception as exc:  # noqa: BLE001 - CLI supervision boundary
                print(f"{exc.__class__.__name__}: {exc}", file=sys.stderr, flush=True)
                result = 1
            if config.once:
                return result
            time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
