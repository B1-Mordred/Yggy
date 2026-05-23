#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import shutil
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if __name__ == "__main__" and sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

from registry_lib import RegistryError, api_key_from_env, api_request, load_local_env


DEFAULT_LOCK_PATH = "/tmp/yggy-capability-implementation-runner.lock"
DEFAULT_IMPLEMENTATION_SCRIPT = ROOT / "scripts" / "implement_capability_plan.py"


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
    )


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
    command.extend(config.extra_args)
    return command


def child_environment() -> dict[str, str]:
    allowed: dict[str, str] = {}
    for key in ("HOME", "PATH", "LANG", "LC_ALL", "SHELL", "USER", "LOGNAME"):
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

    completed = subprocess.run(
        command,
        cwd=config.source_root,
        env=child_environment(),
        timeout=config.command_timeout or None,
    )
    if completed.returncode != 0 and config.mark_failed_on_wrapper_error:
        mark_failed_if_unclaimed(config, api_key, run["id"], completed.returncode)
    return completed.returncode


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


def process_once(config: RunnerConfig, api_key: str) -> int:
    runs = list_queued_runs(config, api_key)
    if not runs:
        print("No queued capability implementation runs.", flush=True)
        return 0

    failures = 0
    for run in runs:
        result = process_run(config, api_key, run)
        if result != 0:
            failures += 1
    return 1 if failures else 0


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
