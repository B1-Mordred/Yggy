from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from render_task_template import values_from_args  # noqa: E402
from task_template_lib import TemplateError, load_templates, render_task_from_template  # noqa: E402


EXPECTED_TEMPLATE_IDS = {
    "topic_digest",
    "server_health",
    "backup_verification",
    "n8n_webhook",
}


def test_all_task_templates_load():
    templates = load_templates()

    assert set(templates) == EXPECTED_TEMPLATE_IDS
    for template in templates.values():
        assert template.default_approval_level.value.startswith("L")
        assert template.allowed_output_targets
        assert template.required_fields == ["id", "name"]
        assert template.safety_notes


@pytest.mark.parametrize("template_id", sorted(EXPECTED_TEMPLATE_IDS))
def test_rendered_templates_produce_valid_disabled_safe_tasks(template_id):
    task = render_task_from_template(
        template_id,
        {
            "id": f"rendered_{template_id}_task",
            "name": f"Rendered {template_id} Task",
        },
    )

    assert task["type"] == template_id
    assert task["enabled"] is False
    assert task["runtime"]["dry_run"] is True
    assert task["policy"]["allow_shell"] is False
    assert task["policy"]["allow_docker_socket"] is False
    assert task["policy"]["allow_external_side_effects"] is False
    assert task["policy"]["allow_filesystem_write"] is False


def test_topic_digest_template_renders_approved_source_ids():
    task = render_task_from_template(
        "topic_digest",
        {
            "id": "rendered_topic_digest_task",
            "name": "Rendered Topic Digest Task",
            "source_ids": ["open_webui_releases", "ollama_releases"],
            "include": ["Open WebUI", "Ollama"],
            "exclude": ["sponsored"],
        },
    )

    assert [source["source_id"] for source in task["sources"]] == ["open_webui_releases", "ollama_releases"]
    assert all(source["url"].startswith("https://") for source in task["sources"])
    assert task["filters"]["include"] == ["Open WebUI", "Ollama"]
    assert task["filters"]["exclude"] == ["sponsored"]


def test_server_health_template_renders_approved_check_ids():
    task = render_task_from_template(
        "server_health",
        {
            "id": "rendered_selected_server_health",
            "name": "Rendered Selected Server Health",
            "check_ids": ["automation_api", "automation_worker", "n8n"],
        },
    )

    assert [check["name"] for check in task["checks"]] == ["automation_api", "automation_worker", "n8n"]
    assert task["checks"][1]["type"] == "worker_heartbeat"


def test_n8n_template_renders_approved_webhook_id():
    task = render_task_from_template(
        "n8n_webhook",
        {
            "id": "rendered_selected_n8n",
            "name": "Rendered Selected n8n",
            "webhook_id": "daily_briefing_stub",
            "n8n_payload": {"description": "bounded test payload"},
        },
    )

    assert task["n8n"]["webhook_id"] == "daily_briefing_stub"
    assert task["n8n"]["path"] == "/webhook/yggy-daily-briefing"
    assert task["n8n"]["payload"] == {"description": "bounded test payload"}


def test_render_script_values_support_gateway_fields():
    values = values_from_args(
        SimpleNamespace(
            task_id="rendered_cli_task",
            name="Rendered CLI Task",
            cron=None,
            timezone=None,
            output_target="n8n",
            source_ids=None,
            check_ids=["automation_api"],
            webhook_id="daily_briefing_stub",
            n8n_payload_json='{"description":"bounded"}',
            include=None,
            exclude=None,
            max_items=None,
            owner=None,
            created_by=None,
        )
    )

    assert values["check_ids"] == ["automation_api"]
    assert values["webhook_id"] == "daily_briefing_stub"
    assert values["n8n_payload"] == {"description": "bounded"}


def test_render_script_values_reject_invalid_n8n_payload_json():
    with pytest.raises(TemplateError) as exc:
        values_from_args(
            SimpleNamespace(
                task_id="rendered_cli_task",
                name="Rendered CLI Task",
                cron=None,
                timezone=None,
                output_target=None,
                source_ids=None,
                check_ids=None,
                webhook_id=None,
                n8n_payload_json="[]",
                include=None,
                exclude=None,
                max_items=None,
                owner=None,
                created_by=None,
            )
        )

    assert "JSON object" in str(exc.value)


def test_invalid_output_target_is_rejected():
    with pytest.raises(TemplateError) as exc:
        render_task_from_template(
            "server_health",
            {
                "id": "bad_target_server_health",
                "name": "Bad Target Server Health",
                "output_target": "briefings",
            },
        )

    assert "not allowed" in str(exc.value)


def test_runtime_dry_run_false_is_rejected():
    with pytest.raises(TemplateError) as exc:
        render_task_from_template(
            "topic_digest",
            {
                "id": "live_runtime_template_task",
                "name": "Live Runtime Template Task",
                "dry_run": False,
            },
        )

    assert "must remain runtime.dry_run=true" in str(exc.value)


def test_unknown_template_is_rejected():
    with pytest.raises(TemplateError) as exc:
        render_task_from_template("unknown_template", {"id": "unknown_task", "name": "Unknown Task"})

    assert "unknown task template" in str(exc.value)


def test_required_fields_are_enforced():
    with pytest.raises(TemplateError) as exc:
        render_task_from_template("topic_digest", {"id": "missing_name_task"})

    assert "required template values missing: name" in str(exc.value)


def test_unknown_source_id_is_rejected():
    with pytest.raises(TemplateError) as exc:
        render_task_from_template(
            "topic_digest",
            {
                "id": "bad_source_topic_digest",
                "name": "Bad Source Topic Digest",
                "source_ids": ["not_registered"],
            },
        )

    assert "not enabled in approved_sources.yaml" in str(exc.value)
