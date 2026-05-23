from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import implement_capability_plan  # noqa: E402


def generic_proposal() -> dict:
    return {
        "id": "proposal-123",
        "title": "HTTP JSON Metric Threshold",
        "purpose": "Monitor approved JSON metric endpoints and alert when numeric thresholds are crossed.",
        "suggested_capability_id": "http_json_metric_threshold.v1",
        "suggested_task_type": "http_json_metric_threshold",
        "likely_approval_level": "L1_NOTIFY_ONLY",
        "required_inputs": ["approved endpoint ID", "JSON path", "threshold", "schedule"],
        "safety_rules": ["must use approved endpoint IDs", "must not accept arbitrary URLs"],
        "non_goals": ["no shell execution", "no Docker access"],
        "implementation_plan": {
            "id": "plan-123",
            "status": "implementation_planned",
            "summary": "Implement a bounded metric threshold monitor.",
            "files_to_change": [
                "configs/capabilities.yaml",
                "configs/metrics/endpoints.yaml",
                "configs/task_templates/http_json_metric_threshold.yaml",
                "automation-api/app/schemas.py",
                "automation-api/app/services/task_template_service.py",
                "automation-api/tests/test_task_templates.py",
                "automation-worker/worker/main.py",
                "automation-worker/worker/handlers/http_json_metric_threshold.py",
                "automation-worker/tests/test_http_json_metric_threshold.py",
                "docs/TASK_SCHEMA.md",
            ],
            "required_decisions": [],
            "security_boundaries": [],
            "acceptance_tests": [],
        },
    }


def test_staged_harness_derives_paths_from_proposal_without_capability_specific_payloads():
    stages = implement_capability_plan.build_implementation_stages(generic_proposal())
    stage_ids = [stage["id"] for stage in stages]
    allowed_paths = {path for stage in stages for path in stage["allowed_paths"]}

    assert stage_ids == [
        "registry_config",
        "task_template",
        "api_validation_rendering",
        "worker_handler",
        "docs_final_tests",
    ]
    assert "configs/metrics/endpoints.yaml" in allowed_paths
    assert "configs/task_templates/http_json_metric_threshold.yaml" in allowed_paths
    assert "automation-worker/worker/handlers/http_json_metric_threshold.py" in allowed_paths
    assert "automation-worker/tests/test_http_json_metric_threshold.py" in allowed_paths

    source = Path(implement_capability_plan.__file__).read_text(encoding="utf-8")
    assert "tls_certificate_expiry_payloads" not in source
    assert "yggy_ops_https" not in source
    assert "build_tls_" not in source


def test_stage_prompt_is_proposal_driven_and_keeps_execution_boundaries():
    proposal = generic_proposal()
    stage = implement_capability_plan.build_implementation_stages(proposal)[0]

    prompt = implement_capability_plan.build_stage_prompt(
        proposal,
        stage=stage,
        stage_index=1,
        stage_count=5,
        repo_root=ROOT,
        existing_changes=[],
    )

    assert "http_json_metric_threshold.v1" in prompt
    assert "configs/metrics/endpoints.yaml" in prompt
    assert "Do not approve, run live automations, deploy, push, use Docker" in prompt
    assert "New registries must be explicit allowlists" in prompt
    assert "existing_capability_ids_must_remain" in prompt
    assert "append new entries instead of replacing" in prompt
    assert "Do not create or edit `proposals/` files" in prompt
    assert "Yggy harness constraints for local code models, including Qwen3-Coder" in prompt
    assert "Allowed repository paths for this stage are exact" in prompt
    assert "no shell execution by Bragi" in prompt
    assert "no Docker socket access" in prompt
    assert "Heimdal validates before any Yggdrasil canonical action" in prompt
    assert "copy every mandatory boundary below verbatim" in prompt
    assert "`capabilities/`" in prompt
    assert not prompt.startswith("/goal")
    assert "yggy_ops_https" not in prompt


def test_one_shot_prompt_includes_qwen3_harness_contract():
    proposal = generic_proposal()

    prompt = implement_capability_plan.build_implementation_prompt(
        proposal,
        profile="capability-implementer",
        model="hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:UD-Q4_K_XL",
        repo_root=ROOT,
    )

    assert "Yggy harness constraints for local code models, including Qwen3-Coder" in prompt
    assert "Treat implementation_plan.files_to_change" in prompt
    assert "Do not invent new top-level directories" in prompt
    assert "configs/task_templates/http_json_metric_threshold.yaml" in prompt
    assert "no admin approvals or approval nonces" in prompt
    assert "task templates remain disabled and dry-run by default" in prompt
    assert "Heimdal validates before any Yggdrasil canonical action" in prompt


def test_stage_prompt_can_opt_into_hermes_goal_command():
    proposal = generic_proposal()
    stage = implement_capability_plan.build_implementation_stages(proposal)[0]

    prompt = implement_capability_plan.build_stage_prompt(
        proposal,
        stage=stage,
        stage_index=1,
        stage_count=5,
        repo_root=ROOT,
        existing_changes=[],
        use_goal_command=True,
    )

    assert prompt.startswith("/goal Continue implementing")


def test_registry_stage_allows_derived_config_registries_under_configs_only():
    proposal = generic_proposal()
    proposal["implementation_plan"]["files_to_change"] = [
        path
        for path in proposal["implementation_plan"]["files_to_change"]
        if not path.startswith("configs/metrics/")
    ]

    registry_stage = implement_capability_plan.build_implementation_stages(proposal)[0]

    assert "configs/metrics/" in registry_stage["allowed_paths"]
    assert "metrics/" not in registry_stage["allowed_paths"]
    assert implement_capability_plan.path_allowed("configs/metrics/endpoints.yaml", set(registry_stage["allowed_paths"]))
    assert not implement_capability_plan.path_allowed("metrics/metric-endpoints.yaml", set(registry_stage["allowed_paths"]))


def test_deterministic_registry_seed_appends_without_replacing_existing_entries(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    capabilities_path = config_dir / "capabilities.yaml"
    capabilities_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "capabilities": [
                    {
                        "id": "existing_capability.v1",
                        "purpose": "Keep me intact.",
                        "maps_to_task_type": "existing_capability",
                        "safety_rules": ["Existing rule."],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    implement_capability_plan.seed_capability_registry_entry(tmp_path, generic_proposal())

    data = yaml.safe_load(capabilities_path.read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in data["capabilities"]}

    assert entries["existing_capability.v1"]["purpose"] == "Keep me intact."
    assert entries["http_json_metric_threshold.v1"]["maps_to_task_type"] == "http_json_metric_threshold"
    assert entries["http_json_metric_threshold.v1"]["required_slots"]
    assert "shell" in entries["http_json_metric_threshold.v1"]["unsafe_keywords"]


def test_deterministic_task_template_seed_creates_disabled_dry_run_template(tmp_path):
    (tmp_path / "configs" / "task_templates").mkdir(parents=True)

    implement_capability_plan.seed_task_template(tmp_path, generic_proposal())

    template_path = tmp_path / "configs" / "task_templates" / "http_json_metric_threshold.yaml"
    data = yaml.safe_load(template_path.read_text(encoding="utf-8"))

    assert data["id"] == "http_json_metric_threshold"
    assert data["task_type"] == "http_json_metric_threshold"
    assert data["defaults"]["enabled"] is False
    assert data["defaults"]["runtime"]["dry_run"] is True
    assert data["defaults"]["policy"]["allow_shell"] is False


def test_only_deterministic_config_stages_are_skippable():
    assert implement_capability_plan.DETERMINISTIC_SEED_STAGE_IDS == {"registry_config", "task_template"}


def test_hermes_runner_ignores_ambient_rules():
    source = Path(implement_capability_plan.__file__).read_text(encoding="utf-8")

    assert '"--ignore-rules"' in source
    assert "without stage-specific repository changes" in source
