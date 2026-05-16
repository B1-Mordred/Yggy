#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERMES_ROOT = Path('/srv/hermes')
INBOX = HERMES_ROOT / 'workspaces' / 'management' / 'inbox'
REPORTS = HERMES_ROOT / 'reports' / 'management'
BRIEF_CONFIG = HERMES_ROOT / 'briefs' / 'subjects.yaml'
MGMT_MODULE = HERMES_ROOT / 'scripts' / 'hermes_mgmt.py'
MODEL_ID = os.environ.get('API_SERVER_MODEL_NAME') or 'webui'
DISPLAY_NAME = 'Yggdrasil'
API_KEY = os.environ.get('API_SERVER_KEY', '').strip()
HOST = os.environ.get('API_SERVER_HOST', '127.0.0.1')
PORT = int(os.environ.get('API_SERVER_PORT', '8642'))
INTENT_ENABLED = os.environ.get('YGGDRASIL_INTENT_ENABLED', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}
INTENT_MODEL = os.environ.get('YGGDRASIL_INTENT_MODEL', 'granite4.1:8b').strip()
INTENT_BASE_URL = os.environ.get('YGGDRASIL_INTENT_BASE_URL', 'http://127.0.0.1:11434/v1').rstrip('/')
INTENT_TIMEOUT = int(os.environ.get('YGGDRASIL_INTENT_TIMEOUT', '30'))
INTENT_MIN_CONFIDENCE = float(os.environ.get('YGGDRASIL_INTENT_MIN_CONFIDENCE', '0.70'))
AUTOMATION_API_BASE_URL = os.environ.get('AUTOMATION_API_BASE_URL', 'http://127.0.0.1:8088').rstrip('/')
AUTOMATION_TOOL_API_KEY = os.environ.get('AUTOMATION_TOOL_API_KEY', '').strip()
PROPOSAL_RE = re.compile(r'\b(\d{14}_[A-Za-z0-9._-]+)\b')
AUTOMATION_TASK_ALIASES = {
    'daily local ai security briefing': 'daily_local_ai_security_briefing',
    'daily local ai/security briefing': 'daily_local_ai_security_briefing',
    'local ai security briefing': 'daily_local_ai_security_briefing',
    'local ai/security briefing': 'daily_local_ai_security_briefing',
}
ALLOWED_SERVICES = {
    'hermes-openwebui-api.service',
    'hermes-openwebui-api-proxy.service',
    'hermes-discord-gateway.service',
    'hermes-dashboard.service',
    'hermes-briefs-web.service',
}
ALLOWED_OPERATIONS: dict[str, set[str]] = {
    'brief': {
        'get_config_summary',
        'update_subject',
        'update_schedule',
        'update_delivery',
        'run_manual',
        'validate',
    },
    'management': {
        'list_pending',
        'status',
        'apply_proposal',
        'cancel_proposal',
        'propose_service_restart',
    },
}
OPERATION_ALIASES = {
    ('brief', 'summary'): 'get_config_summary',
    ('brief', 'show_config'): 'get_config_summary',
    ('brief', 'run'): 'run_manual',
    ('brief', 'generate'): 'run_manual',
    ('brief', 'validate_config'): 'validate',
    ('management', 'pending'): 'list_pending',
    ('management', 'pending_proposals'): 'list_pending',
    ('management', 'apply'): 'apply_proposal',
    ('management', 'approve'): 'apply_proposal',
    ('management', 'cancel'): 'cancel_proposal',
    ('management', 'reject'): 'cancel_proposal',
}
_MGMT_MODULE: Any | None = None


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def stamp() -> str:
    return datetime.now().strftime('%Y%m%d-%H%M%S')


def slug(value: str) -> str:
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', value.strip()).strip('-').lower()
    return value or 'request'


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get('text'), str):
                    parts.append(item['text'])
                elif isinstance(item.get('content'), str):
                    parts.append(item['content'])
            elif isinstance(item, str):
                parts.append(item)
        return '\n'.join(parts)
    return str(content or '')


def latest_user_request(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get('role') == 'user':
            return extract_text(message.get('content')).strip()
    return ''


def prior_conversation_text(messages: list[dict[str, Any]]) -> str:
    return '\n'.join(extract_text(m.get('content')) for m in messages[:-1])


def clean_proposal_id(value: str) -> str:
    value = value.strip().strip('`"\'.,;:!?)]}')
    if value.endswith('.md'):
        value = value[:-3]
    return value


def proposal_ids_from_prior(prior_text: str) -> list[str]:
    ids: list[str] = []
    patterns = [
        r'Do you approve applying proposal\s+`?(\d{14}_[A-Za-z0-9._-]+)`?',
        r'Proposal\s+`?(\d{14}_[A-Za-z0-9._-]+)`?\s+is ready',
        r'Proposal ID:\s*\*{0,2}`?(\d{14}_[A-Za-z0-9._-]+)`?',
        r'Proposal created:\s*(\d{14}_[A-Za-z0-9._-]+)',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, prior_text, re.IGNORECASE):
            candidate = clean_proposal_id(match.group(1))
            if candidate and candidate not in ids:
                ids.append(candidate)
    return ids


def shortcut_intent(user_text: str) -> str | None:
    compact = re.sub(r'\s+', ' ', user_text.strip().lower()).strip(' .!')
    if len(compact) > 80:
        return None
    approve_patterns = [
        r'yes',
        r'yes,? go ahead',
        r'yep',
        r'approved?',
        r'approve it',
        r'i approve',
        r'i approve it',
        r'apply it',
        r'do it',
        r'go ahead',
    ]
    reject_patterns = [
        r'no',
        r'nope',
        r'no,? remove it',
        r'no,? cancel it',
        r'reject',
        r'reject it',
        r'cancel',
        r'cancel it',
        r'remove it',
        r'discard it',
        r'do not apply',
        r"don't apply",
    ]
    if any(re.fullmatch(pattern, compact) for pattern in approve_patterns):
        return 'approve'
    if any(re.fullmatch(pattern, compact) for pattern in reject_patterns):
        return 'cancel'
    return None


def normalize_approval_shortcut(user_text: str, prior_text: str) -> str:
    intent = shortcut_intent(user_text)
    if intent is None:
        return user_text
    ids = proposal_ids_from_prior(prior_text)
    if not ids:
        return user_text
    last_id = ids[-1]
    if intent == 'approve':
        return f'approve proposal {last_id}'
    return f'cancel proposal {last_id}'


def openwebui_auxiliary_answer(user_text: str) -> str | None:
    lowered = user_text.lower()
    if '### task:' not in lowered:
        return None
    if 'suggest 3-5 relevant follow-up questions' in lowered:
        return '{"follow_ups":[]}'
    if 'generate a concise, 3-5 word title' in lowered:
        return '{"title":"Yggdrasil Management"}'
    if 'generate 1-3 broad tags' in lowered:
        return '{"tags":["Management","Hermes"]}'
    return None


def read_subject_names() -> list[str]:
    if not BRIEF_CONFIG.exists():
        return []
    text = BRIEF_CONFIG.read_text(encoding='utf-8', errors='replace')
    return [match.group(1).strip() for match in re.finditer(r'(?m)^\s*-\s+name:\s*(.+?)\s*$', text)]


def load_mgmt_module() -> Any | None:
    global _MGMT_MODULE
    if _MGMT_MODULE is not None:
        return _MGMT_MODULE
    try:
        spec = importlib.util.spec_from_file_location('yggdrasil_hermes_mgmt', MGMT_MODULE)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules['yggdrasil_hermes_mgmt'] = module
        spec.loader.exec_module(module)
        _MGMT_MODULE = module
        return module
    except Exception as exc:
        print(f'deterministic dispatcher import failed: {exc}', file=sys.stderr)
        return None


def deterministic_interpret(user_request: str) -> dict[str, Any] | None:
    module = load_mgmt_module()
    if module is None:
        return None
    try:
        interpreted = module.infer_dispatch_from_text(user_request)
        return interpreted if isinstance(interpreted, dict) else None
    except Exception:
        return None


def coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'1', 'true', 'yes', 'on', 'enable', 'enabled'}:
            return True
        if lowered in {'0', 'false', 'no', 'off', 'disable', 'disabled'}:
            return False
    raise ValueError(f'{name} must be boolean')


def coerce_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception as exc:
        raise ValueError(f'{name} must be an integer') from exc
    if not minimum <= number <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return number


def string_list(value: Any, name: str, *, required: bool = False, limit: int = 10) -> list[str] | None:
    if value is None:
        if required:
            raise ValueError(f'{name} is required')
        return None
    if not isinstance(value, list):
        raise ValueError(f'{name} must be a list')
    items = [str(item).strip() for item in value if str(item).strip()]
    if required and not items:
        raise ValueError(f'{name} may not be empty')
    if len(items) > limit:
        raise ValueError(f'{name} may not contain more than {limit} items')
    if any(re.search(r"[{}]|\b(source|sort_by|instant_buy)\s*:", item) for item in items):
        raise ValueError(f'{name} must contain plain search strings, not structured pseudo-objects')
    return items


def canonical_operation(domain: str, operation: str) -> str:
    operation = operation.strip().lower().replace('-', '_')
    return OPERATION_ALIASES.get((domain, operation), operation)


def sanitize_action(interpreted: dict[str, Any], *, source: str, user_request: str = '') -> tuple[dict[str, Any] | None, str | None]:
    route = str(interpreted.get('route') or 'action').strip().lower()
    if route in {'clarify', 'question'}:
        question = str(interpreted.get('question') or interpreted.get('reason') or '').strip()
        return None, question or 'What exactly should I change?'
    if route not in {'action', 'execute'}:
        reason = str(interpreted.get('reason') or '').strip()
        return None, reason or 'I could not map that to an approved Yggdrasil action.'

    if source == 'llm':
        confidence = float(interpreted.get('confidence') or 0)
        if confidence < INTENT_MIN_CONFIDENCE:
            question = str(interpreted.get('question') or '').strip()
            return None, question or 'I am not confident enough to apply that. Please phrase the requested action more directly.'

    domain = str(interpreted.get('domain') or '').strip().lower()
    operation = canonical_operation(domain, str(interpreted.get('operation') or ''))
    if domain not in ALLOWED_OPERATIONS or operation not in ALLOWED_OPERATIONS[domain]:
        return None, f'That request is outside the approved Yggdrasil action set: {domain}/{operation}.'

    raw_params = interpreted.get('params')
    params = raw_params if isinstance(raw_params, dict) else {}
    cleaned: dict[str, Any] = {}
    lowered_request = user_request.lower()

    if domain == 'brief' and operation == 'update_subject':
        if source == 'llm' and not re.search(
            r"\b(add|create|include|change|set|update|remove|delete|drop|replace|rename|subject|items?|stories?|articles?|offers?|listings?|deals?)\b",
            lowered_request,
        ):
            return None, 'I did not see an explicit brief-subject change request, so I did not change the brief configuration.'
        subject = str(params.get('subject') or params.get('name') or '').strip()
        if not subject:
            return None, 'Which brief subject should I update or create?'
        existing = {name.lower() for name in read_subject_names()}
        cleaned['subject'] = subject
        if 'max_items' in params:
            cleaned['max_items'] = coerce_int(params['max_items'], 'max_items', 1, 10)
        if 'recency_hours' in params:
            cleaned['recency_hours'] = coerce_int(params['recency_hours'], 'recency_hours', 1, 168)
        for key in ('avoid_duplicates', 'dedupe_against_other_subjects', 'create', 'remove'):
            if key in params:
                cleaned[key] = coerce_bool(params[key], key)
        queries = string_list(params.get('queries'), 'queries', required=False, limit=8)
        if queries is not None:
            cleaned['queries'] = queries
        standards = string_list(params.get('quality_standards'), 'quality_standards', required=False, limit=8)
        if standards is not None:
            cleaned['quality_standards'] = standards
        will_create = cleaned.get('create', True) is not False and subject.lower() not in existing and not cleaned.get('remove', False)
        if will_create and not cleaned.get('queries'):
            return None, 'A new brief subject needs at least one search query before I can add it safely.'

    elif domain == 'brief' and operation == 'update_schedule':
        for key in ('morning', 'evening', 'randomized_delay'):
            if key in params:
                cleaned[key] = str(params[key]).strip()
        if not cleaned:
            return None, 'Which brief schedule value should I change?'

    elif domain == 'brief' and operation == 'update_delivery':
        platform = str(params.get('platform') or '').strip().lower()
        if platform not in {'discord', 'web'}:
            return None, 'Brief delivery platform must be discord or web.'
        if 'enabled' not in params:
            return None, 'Should that delivery platform be enabled or disabled?'
        cleaned['platform'] = platform
        cleaned['enabled'] = coerce_bool(params['enabled'], 'enabled')

    elif domain == 'brief' and operation == 'run_manual':
        period = str(params.get('period') or 'manual').strip().lower()
        if period not in {'manual', 'morning', 'evening'}:
            return None, 'Brief run period must be manual, morning, or evening.'
        cleaned['period'] = period

    elif domain == 'management' and operation in {'apply_proposal', 'cancel_proposal'}:
        proposal_id = clean_proposal_id(str(params.get('proposal_id') or ''))
        if not PROPOSAL_RE.fullmatch(proposal_id):
            return None, f'{operation} requires a valid proposal ID.'
        cleaned['proposal_id'] = proposal_id

    elif domain == 'management' and operation == 'propose_service_restart':
        service = str(params.get('service') or '').strip()
        if service not in ALLOWED_SERVICES:
            return None, 'Service restart proposals are limited to approved Hermes services.'
        cleaned['service'] = service

    elif domain == 'brief' and operation in {'get_config_summary', 'validate'}:
        cleaned = {}

    elif domain == 'management' and operation in {'list_pending', 'status'}:
        cleaned = {}

    return {'domain': domain, 'operation': operation, 'params': cleaned, 'source': source}, None


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    start = stripped.find('{')
    end = stripped.rfind('}')
    if start == -1 or end <= start:
        raise ValueError('intent model returned no JSON object')
    value = json.loads(stripped[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError('intent model JSON was not an object')
    return value


def llm_interpret(user_request: str, prior_text: str) -> dict[str, Any] | None:
    if not INTENT_ENABLED or not INTENT_MODEL:
        return None
    subjects = read_subject_names()
    subject_lines = '\n'.join(f'- {name}' for name in subjects) or '- none'
    system = (
        'Return one JSON object only. You are an intent parser, not an executor. '
        'Allowed domains/operations: '
        'brief/get_config_summary, brief/update_subject, brief/update_schedule, brief/update_delivery, brief/run_manual, brief/validate, '
        'management/list_pending, management/status, management/apply_proposal, management/cancel_proposal, management/propose_service_restart. '
        'JSON for actions: {"route":"action","domain":"brief|management","operation":"...","params":{},"confidence":0.0-1.0}. '
        'JSON for uncertainty: {"route":"clarify","question":"short question","confidence":0.0-1.0}. '
        'Use management/list_pending for pending approvals/proposals. '
        'Use brief/run_manual with period manual|morning|evening for brief generation. '
        'Use brief/update_subject for adding/updating/removing brief subjects. New subjects require subject, max_items when specified, and non-empty queries. '
        'For eBay.de offer subjects, include eBay.de queries and quality_standards covering active listings, price, condition, seller signal, shipping/location, avoiding defective/parts-only listings, and not inventing prices. '
        'Use management/propose_service_restart only for approved Hermes service restarts; never direct restart. '
        'If unsupported or vague, return clarify.'
    )
    user = (
        f'Current brief subjects:\n{subject_lines}\n\n'
        f'Prior conversation excerpt:\n{prior_text[-1000:]}\n\n'
        f'Latest user request:\n{user_request}'
    )
    payload = {
        'model': INTENT_MODEL,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'temperature': 0,
        'stream': False,
        'response_format': {'type': 'json_object'},
    }
    request = urllib.request.Request(
        f'{INTENT_BASE_URL}/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=INTENT_TIMEOUT) as response:
            raw = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f'intent model call failed: {exc}', file=sys.stderr)
        return None
    content = (((raw.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
    try:
        return extract_json_object(content)
    except Exception as exc:
        print(f'intent model JSON parse failed: {exc}: {content[:500]}', file=sys.stderr)
        return None


def build_dispatch(user_request: str, prior_text: str) -> tuple[dict[str, Any] | None, str | None]:
    compact = re.sub(r'\s+', ' ', user_request.strip().lower()).strip(' .!')
    if compact in {'ok', 'okay', 'thanks', 'thank you', 'thats it', "that's it", 'that is it'}:
        return None, 'No action requested.'

    deterministic = deterministic_interpret(user_request)
    if deterministic is not None:
        action, question = sanitize_action({'route': 'action', **deterministic}, source='deterministic', user_request=user_request)
        if action is not None:
            return action, None
        if question:
            return None, question

    interpreted = llm_interpret(user_request, prior_text)
    if interpreted is not None:
        action, question = sanitize_action(interpreted, source='llm', user_request=user_request)
        if action is not None:
            return action, None
        if question:
            return None, question

    return None, (
        'I could not map that to an approved Yggdrasil action. '
        'Try asking for a brief configuration change, schedule change, delivery toggle, manual brief run, pending proposal list, or proposal approval/cancel.'
    )


def automation_request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    if not AUTOMATION_TOOL_API_KEY:
        return 503, {'detail': 'AUTOMATION_TOOL_API_KEY is not configured for Yggdrasil.'}
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(
        f'{AUTOMATION_API_BASE_URL}{path}',
        data=data,
        method=method,
        headers={
            'Content-Type': 'application/json',
            'X-Automation-Api-Key': AUTOMATION_TOOL_API_KEY,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode('utf-8')
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            detail: Any = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw
        return exc.code, detail
    except urllib.error.URLError as exc:
        return 503, {'detail': f'automation API unavailable: {exc}'}


def automation_task_id_from_text(text: str) -> str | None:
    lowered = text.lower()
    for phrase, task_id in AUTOMATION_TASK_ALIASES.items():
        if phrase in lowered:
            return task_id
    match = re.search(r'\b([a-z][a-z0-9]+(?:_[a-z0-9]+)+)\b', text)
    return match.group(1) if match else None


def parse_schedule(text: str) -> tuple[str, str]:
    match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', text)
    hour = int(match.group(1)) if match else 8
    minute = int(match.group(2)) if match else 0
    weekday = bool(re.search(r'\b(weekday|weekdays|monday|mon-fri|workday|workdays)\b', text, re.IGNORECASE))
    day_part = '1-5' if weekday else '*'
    return f'{minute} {hour} * * {day_part}', 'Europe/Berlin'


def local_ai_security_briefing_draft(text: str) -> dict[str, Any]:
    cron, timezone = parse_schedule(text)
    return {
        'id': 'daily_local_ai_security_briefing',
        'name': 'Daily Local AI Security Briefing',
        'type': 'topic_digest',
        'enabled': False,
        'owner': 'local_user',
        'created_by': 'yggdrasil',
        'trigger': {'kind': 'schedule', 'cron': cron, 'timezone': timezone},
        'sources': [
            {'type': 'web_query', 'query': 'Open WebUI Ollama Hermes Agent Docker n8n local AI security'},
            {'type': 'web_query', 'query': 'self-hosted local LLM automation security approvals Docker'},
        ],
        'filters': {
            'include': ['Open WebUI', 'Ollama', 'Hermes', 'Docker', 'n8n', 'local AI security'],
            'exclude': ['sponsored', 'rumor'],
        },
        'output': {
            'channel': 'discord',
            'target': 'briefings',
            'format': '5 bullets, impact, source links, recommended action',
        },
        'policy': {
            'approval_level': 'L1_NOTIFY_ONLY',
            'max_items': 10,
            'require_sources': True,
            'allow_external_side_effects': False,
            'allow_shell': False,
            'allow_docker_socket': False,
            'allow_filesystem_write': False,
        },
        'runtime': {'dry_run': True, 'timeout_seconds': 120, 'retry_count': 1},
    }


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    text = str(value)
    if not text or any(char in text for char in ':#[]{},"\n') or text.strip() != text:
        return json.dumps(text)
    return text


def to_yaml(value: Any, indent: int = 0) -> str:
    pad = ' ' * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f'{pad}{key}:')
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f'{pad}{key}: {yaml_scalar(item)}')
        return '\n'.join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                lines.append(f'{pad}- {next(iter(item.keys()))}: {yaml_scalar(next(iter(item.values())))}')
                for key, subitem in list(item.items())[1:]:
                    if isinstance(subitem, (dict, list)):
                        lines.append(f'{pad}  {key}:')
                        lines.append(to_yaml(subitem, indent + 4))
                    else:
                        lines.append(f'{pad}  {key}: {yaml_scalar(subitem)}')
            elif isinstance(item, list):
                lines.append(f'{pad}-')
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f'{pad}- {yaml_scalar(item)}')
        return '\n'.join(lines)
    return f'{pad}{yaml_scalar(value)}'


def format_task(task: dict[str, Any]) -> str:
    config = task.get('config') or {}
    trigger = config.get('trigger') or {}
    output = config.get('output') or {}
    policy = config.get('policy') or {}
    runtime = config.get('runtime') or {}
    return (
        f"Task `{task.get('id')}`\n\n"
        f"- Name: {task.get('name')}\n"
        f"- Type: `{task.get('type')}`\n"
        f"- Enabled: `{str(task.get('enabled')).lower()}`\n"
        f"- Status: `{task.get('status')}`\n"
        f"- Approval level: `{task.get('approval_level')}`\n"
        f"- Trigger: `{trigger.get('cron', 'n/a')}` `{trigger.get('timezone', 'n/a')}`\n"
        f"- Output: `{output.get('channel', 'n/a')}` target `{output.get('target', 'n/a')}`\n"
        f"- Dry run: `{str(runtime.get('dry_run', True)).lower()}`\n"
        f"- Shell allowed: `{str(policy.get('allow_shell', False)).lower()}`\n"
        f"- Docker socket allowed: `{str(policy.get('allow_docker_socket', False)).lower()}`\n\n"
        "Approval note: L1 recurring notification tasks require initial local approval. "
        "I can request approval, but I cannot approve it myself or use the admin key."
    )


def format_task_list(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return 'No automation tasks are registered yet.'
    lines = ['Automation tasks:']
    for task in tasks:
        lines.append(
            f"- `{task.get('id')}`: {task.get('name')} "
            f"({task.get('approval_level')}, enabled `{str(task.get('enabled')).lower()}`, status `{task.get('status')}`)"
        )
    return '\n'.join(lines)


def format_draft_response(status_code: int, body: Any, draft: dict[str, Any]) -> str:
    if status_code == 201 and isinstance(body, dict):
        task = body.get('task') or {}
        approval = body.get('approval')
        approval_text = ''
        if approval:
            approval_text = (
                f"\n\nApproval request created:\n"
                f"- Approval ID: `{approval.get('id')}`\n"
                f"- Approval level: `{approval.get('approval_level')}`\n"
                f"- Status: `{approval.get('status')}`\n"
                "- Approve only through the local admin CLI/UI. Do not paste admin secrets into chat."
            )
        return (
            f"Draft task `{task.get('id')}` was created and remains disabled.\n\n"
            f"{format_task(task)}"
            f"{approval_text}\n\n"
            "YAML draft:\n"
            f"```yaml\n{to_yaml(draft)}\n```"
        )
    if status_code == 409:
        task_id = draft['id']
        get_status, existing = automation_request('GET', f'/tasks/{task_id}')
        if get_status == 200 and isinstance(existing, dict):
            return (
                f"Task `{task_id}` already exists, so I did not create a duplicate.\n\n"
                f"{format_task(existing)}\n\n"
                "Current YAML shape for the requested draft would be:\n"
                f"```yaml\n{to_yaml(draft)}\n```"
            )
    return f'Automation API rejected the draft with status `{status_code}`:\n\n```json\n{json.dumps(body, indent=2)}\n```'


def handle_automation_request(user_text: str) -> str | None:
    lowered = user_text.lower()
    automation_words = ('automation', 'automations', 'task', 'tasks', 'control plane')
    project_words = ('local ai security briefing', 'daily local ai security briefing', 'open webui', 'ollama', 'yggdrasil')
    if not any(word in lowered for word in automation_words + project_words):
        return None

    if re.search(r'\b(list|show all|what .*tasks|tasks\?)\b', lowered) and re.search(r'\b(task|tasks|automation|automations)\b', lowered):
        status_code, body = automation_request('GET', '/tasks')
        if status_code == 200 and isinstance(body, list):
            return format_task_list(body)
        return f'Automation API returned status `{status_code}` while listing tasks:\n\n```json\n{json.dumps(body, indent=2)}\n```'

    if re.search(r'\b(draft|create|add|schedule|new)\b', lowered):
        if 'brief' not in lowered and 'briefing' not in lowered:
            return (
                'I can draft automation tasks through the control plane, but I need the task purpose, trigger, '
                'sources, output target, and approval level.'
            )
        draft = local_ai_security_briefing_draft(user_text)
        status_code, body = automation_request('POST', '/tasks/draft', draft)
        return format_draft_response(status_code, body, draft)

    if re.search(r'\b(request approval|approval request|ask.*approval)\b', lowered):
        task_id = automation_task_id_from_text(user_text)
        if not task_id:
            return 'Which automation task should I request approval for?'
        status_code, body = automation_request('POST', f'/tasks/{task_id}/request-approval')
        if status_code in {200, 201}:
            return (
                f"Approval requested for task `{task_id}`.\n\n"
                f"Approval ID: `{body.get('id')}`\n"
                f"Approval level: `{body.get('approval_level')}`\n"
                "Approve only through the local admin CLI/UI."
            )
        return f'Automation API returned status `{status_code}` while requesting approval:\n\n```json\n{json.dumps(body, indent=2)}\n```'

    if re.search(r'\b(show|get|inspect|details?|status)\b', lowered):
        task_id = automation_task_id_from_text(user_text)
        if not task_id and 'automation' in lowered:
            status_code, body = automation_request('GET', '/tasks')
            if status_code == 200 and isinstance(body, list):
                return format_task_list(body)
        if not task_id:
            return 'Which automation task should I show? Give me the task id.'
        status_code, body = automation_request('GET', f'/tasks/{task_id}')
        if status_code == 200 and isinstance(body, dict):
            return format_task(body)
        return f'Automation API returned status `{status_code}` for task `{task_id}`:\n\n```json\n{json.dumps(body, indent=2)}\n```'

    if re.search(r'\b(run|execute|dry run)\b', lowered):
        task_id = automation_task_id_from_text(user_text)
        if not task_id:
            return 'Which automation task should I run? Give me the task id.'
        status_code, body = automation_request('POST', f'/tasks/{task_id}/run')
        if status_code in {200, 202}:
            return f"Run queued for task `{task_id}`.\n\n```json\n{json.dumps(body, indent=2)}\n```"
        return f'Automation API returned status `{status_code}` while queueing the run:\n\n```json\n{json.dumps(body, indent=2)}\n```'

    if re.search(r'\b(pause|disable|stop)\b', lowered):
        task_id = automation_task_id_from_text(user_text)
        if not task_id:
            return 'Which automation task should I pause? Give me the task id.'
        status_code, body = automation_request('POST', f'/tasks/{task_id}/pause')
        if status_code == 200 and isinstance(body, dict):
            return f"Task `{task_id}` is paused.\n\n{format_task(body)}"
        return f'Automation API returned status `{status_code}` while pausing the task:\n\n```json\n{json.dumps(body, indent=2)}\n```'

    return (
        'This Yggdrasil endpoint is now dedicated to the personal automation control plane. '
        'Ask me to list, show, draft, request approval for, pause, or dry-run automation tasks.'
    )


def write_request(user_request: str, dispatch: dict[str, Any] | None = None) -> Path:
    INBOX.mkdir(parents=True, exist_ok=True)
    if dispatch is None:
        params: dict[str, Any] = {'user_request': user_request}
        source = 'raw'
    else:
        params = {
            'user_request': user_request,
            'domain': dispatch['domain'],
            'operation': dispatch['operation'],
            'action_params': dispatch.get('params') or {},
        }
        source = str(dispatch.get('source') or 'unknown')
    payload = {
        'action': 'dispatch_action',
        'reason': f'Yggdrasil WebUI request ({source} intent): {user_request[:160]}',
        'params': params,
    }
    name = f"request-{stamp()}-yggdrasil-{slug(user_request[:40])}.json"
    final = INBOX / name
    tmp = INBOX / f'.{name}.tmp'
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n'
    with tmp.open('w', encoding='utf-8') as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.rename(final)
    return final


def wait_report(request_file: Path, seconds: int = 45) -> Path | None:
    patterns = [
        f'*processed-management-request-{request_file.name}.md',
        f'*failed-management-request-{request_file.name}.md',
    ]
    deadline = time.time() + seconds
    while True:
        matches = []
        for pattern in patterns:
            matches.extend(REPORTS.glob(pattern))
        matches = sorted(matches, key=lambda p: p.stat().st_mtime)
        if matches:
            return matches[-1]
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def extract(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else None


def path_from_report(raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    return path


def report_title(path: Path | None) -> str:
    if not path or not path.exists():
        return ''
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return ''


def make_answer(user_request: str, request_file: Path, queue_report: Path | None) -> str:
    if queue_report is None:
        return (
            'I queued the request but no host report appeared before the timeout.\n\n'
            f'Request file: `{request_file}`\n'
            f'Expected reports: `{REPORTS}`'
        )
    text = queue_report.read_text(encoding='utf-8', errors='replace')
    failed = 'failed-management-request' in queue_report.name or '- Status: FAILED' in text
    result_report = path_from_report(extract(r'Report:\s*(\S+)', text))
    proposal_id = extract(r'Proposal created:\s*([A-Za-z0-9._-]+)', text)
    applied_id = extract(r'Proposal applied:\s*([A-Za-z0-9._-]+)', text)
    canceled_id = extract(r'Proposal canceled:\s*([A-Za-z0-9._-]+)', text)

    if failed:
        return (
            'I could not complete that through the guarded action pipeline.\n\n'
            f'Failure report: `{queue_report}`'
        )
    if proposal_id:
        title = report_title(result_report)
        title_line = f'\nImpact: {title}' if title else ''
        return (
            f'Proposal `{proposal_id}` is ready.{title_line}\n\n'
            f'Report: `{result_report or queue_report}`\n\n'
            f'Do you approve applying proposal {proposal_id}?'
        )
    if applied_id:
        return f'Applied proposal `{applied_id}`.\n\nReport: `{result_report or queue_report}`'
    if canceled_id:
        return f'Canceled proposal `{canceled_id}`.\n\nReport: `{result_report or queue_report}`'
    if result_report:
        title = report_title(result_report)
        prefix = title or 'The guarded action completed.'
        return f'{prefix}\n\nReport: `{result_report}`'
    return f'The guarded action completed.\n\nReport: `{queue_report}`'


def route_chat(messages: list[dict[str, Any]]) -> str:
    user_text = latest_user_request(messages)
    if not user_text:
        return 'I need an automation-control-plane request.'
    auxiliary_answer = openwebui_auxiliary_answer(user_text)
    if auxiliary_answer is not None:
        return auxiliary_answer
    answer = handle_automation_request(user_text)
    if answer is not None:
        return answer
    return (
        'This Yggdrasil endpoint is dedicated exclusively to the personal automation control plane. '
        'I no longer route Open WebUI requests to the older Hermes brief or management domains. '
        'Ask me to list, show, draft, request approval for, pause, or dry-run automation tasks.'
    )


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def sse_response(handler: BaseHTTPRequestHandler, model: str, content: str) -> None:
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream')
    handler.send_header('Cache-Control', 'no-cache')
    handler.end_headers()
    created = int(time.time())
    chunk = {
        'id': f'chatcmpl-yggdrasil-{created}',
        'object': 'chat.completion.chunk',
        'created': created,
        'model': model,
        'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': content}, 'finish_reason': None}],
    }
    handler.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode('utf-8'))
    done = {
        'id': f'chatcmpl-yggdrasil-{created}',
        'object': 'chat.completion.chunk',
        'created': created,
        'model': model,
        'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
    }
    handler.wfile.write(f"data: {json.dumps(done)}\n\n".encode('utf-8'))
    handler.wfile.write(b'data: [DONE]\n\n')


class Handler(BaseHTTPRequestHandler):
    server_version = 'YggdrasilActionAPI/1.0'

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def authorized(self) -> bool:
        if not API_KEY:
            return True
        header = self.headers.get('Authorization', '')
        return header == f'Bearer {API_KEY}'

    def do_GET(self) -> None:
        if self.path == '/health':
            json_response(self, 200, {
                'status': 'ok',
                'service': 'yggdrasil-action-api',
                'time': now(),
                'intent_enabled': INTENT_ENABLED,
                'intent_model': INTENT_MODEL,
            })
            return
        if self.path == '/v1/models':
            if not self.authorized():
                json_response(self, 401, {'error': {'message': 'unauthorized'}})
                return
            json_response(self, 200, {'object': 'list', 'data': [{
                'id': MODEL_ID,
                'object': 'model',
                'created': int(time.time()),
                'owned_by': 'hermes',
                'permission': [],
                'root': MODEL_ID,
                'parent': None,
            }]})
            return
        json_response(self, 404, {'error': {'message': 'not found'}})

    def do_POST(self) -> None:
        if self.path != '/v1/chat/completions':
            json_response(self, 404, {'error': {'message': 'not found'}})
            return
        if not self.authorized():
            json_response(self, 401, {'error': {'message': 'unauthorized'}})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            messages = payload.get('messages') or []
            if not isinstance(messages, list):
                raise ValueError('messages must be a list')
            answer = route_chat(messages)
            model = str(payload.get('model') or MODEL_ID)
            if payload.get('stream'):
                sse_response(self, model, answer)
                return
            created = int(time.time())
            json_response(self, 200, {
                'id': f'chatcmpl-yggdrasil-{created}',
                'object': 'chat.completion',
                'created': created,
                'model': model,
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
            })
        except Exception as exc:
            json_response(self, 500, {'error': {'message': str(exc), 'type': exc.__class__.__name__}})


def main() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Yggdrasil action API listening on {HOST}:{PORT} as {MODEL_ID}')
    server.serve_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
