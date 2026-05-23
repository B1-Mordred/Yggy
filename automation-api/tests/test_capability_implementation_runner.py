from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "capability_implementation_runner.py"
spec = importlib.util.spec_from_file_location("capability_implementation_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def runner_config(tmp_path: Path, **overrides):
    values = {
        "base_url": "http://api.example",
        "api_key_env": "AUTOMATION_ADMIN_API_KEY",
        "env_root": ROOT,
        "source_root": ROOT,
        "repo_root": tmp_path,
        "managed_workspace": None,
        "python": Path("/usr/bin/python3"),
        "implementation_script": ROOT / "scripts" / "implement_capability_plan.py",
        "poll_seconds": 1.0,
        "batch_size": 2,
        "once": True,
        "dry_run": False,
        "lock_path": tmp_path / "runner.lock",
        "command_timeout": 0,
        "implementation_timeout": 1800,
        "staged": True,
        "fresh_profile": True,
        "allow_dirty": False,
        "no_yolo": False,
        "mark_failed_on_wrapper_error": True,
        "extra_args": (),
        "created_after": "",
    }
    values.update(overrides)
    return runner.RunnerConfig(**values)


def test_runner_command_uses_run_id_and_bounded_defaults(tmp_path):
    config = runner_config(tmp_path)
    command = runner.implementation_command(config, {"id": "run-123"}, tmp_path)

    assert "--run-id" in command
    assert "run-123" in command
    assert "--proposal-id" not in command
    assert "--staged" in command
    assert "--fresh-profile" in command
    assert "--repo-root" in command
    assert str(tmp_path) in command


def test_process_once_invokes_queued_run(monkeypatch, tmp_path):
    config = runner_config(tmp_path)
    calls: list[list[str]] = []

    def fake_api_request(method, path, *, base_url, api_key, payload=None):
        assert base_url == "http://api.example"
        assert api_key == "admin-key"
        assert method == "GET"
        assert path == "/capability-implementation-runs?status=queued&limit=2"
        return [{"id": "run-new", "created_at": "2026-05-23T18:00:00"}]

    def fake_subprocess_run(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == ROOT
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner, "api_request", fake_api_request)
    monkeypatch.setattr(runner, "prepare_repo_root", lambda config: tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", fake_subprocess_run)

    assert runner.process_once(config, "admin-key") == 0
    assert len(calls) == 1
    assert "--run-id" in calls[0]
    assert "run-new" in calls[0]


def test_process_once_marks_unclaimed_failed_on_wrapper_error(monkeypatch, tmp_path):
    config = runner_config(tmp_path)
    patch_payloads: list[dict] = []

    def fake_api_request(method, path, *, base_url, api_key, payload=None):
        if method == "GET" and path == "/capability-implementation-runs?status=queued&limit=2":
            return [{"id": "run-fail", "created_at": "2026-05-23T18:00:00"}]
        if method == "GET" and path == "/capability-implementation-runs/run-fail":
            return {"id": "run-fail", "status": "queued"}
        if method == "PATCH" and path == "/capability-implementation-runs/run-fail":
            patch_payloads.append(payload or {})
            return {"id": "run-fail", "status": "failed"}
        raise AssertionError(f"unexpected API request {method} {path}")

    def fake_subprocess_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 2)

    monkeypatch.setattr(runner, "api_request", fake_api_request)
    monkeypatch.setattr(runner, "prepare_repo_root", lambda config: tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", fake_subprocess_run)

    assert runner.process_once(config, "admin-key") == 1
    assert patch_payloads == [
        {
            "status": "failed",
            "summary": "Capability implementation runner failed while invoking the host-side harness.",
            "error": "scripts/implement_capability_plan.py exited with status 2",
        }
    ]


def test_dry_run_does_not_invoke_subprocess(monkeypatch, tmp_path):
    config = runner_config(tmp_path, dry_run=True)

    monkeypatch.setattr(runner, "prepare_repo_root", lambda config: tmp_path)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess should not run")),
    )

    assert runner.process_run(config, "admin-key", {"id": "run-dry"}) == 0
