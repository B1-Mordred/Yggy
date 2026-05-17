#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
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
RUN_ID_RE = re.compile(r'\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b')
AUTOMATION_TASK_ALIASES = {
    'yggy backup verification': 'yggy_backup_verification',
    'backup verification': 'yggy_backup_verification',
    'backup verifier': 'yggy_backup_verification',
    'backup check': 'yggy_backup_verification',
    'backup health': 'yggy_backup_verification',
    'backup status': 'yggy_backup_verification',
    'backups': 'yggy_backup_verification',
    'server health': 'morning_server_health_check',
    'server health check': 'morning_server_health_check',
    'morning server health': 'morning_server_health_check',
    'morning server health check': 'morning_server_health_check',
    'health check': 'morning_server_health_check',
    'daily brief': 'daily_local_ai_security_briefing',
    'daily briefing': 'daily_local_ai_security_briefing',
    'daily security brief': 'daily_local_ai_security_briefing',
    'daily security briefing': 'daily_local_ai_security_briefing',
    'local ai brief': 'daily_local_ai_security_briefing',
    'local ai briefing': 'daily_local_ai_security_briefing',
    'daily local ai security briefing': 'daily_local_ai_security_briefing',
    'daily local ai/security briefing': 'daily_local_ai_security_briefing',
    'local ai security briefing': 'daily_local_ai_security_briefing',
    'local ai/security briefing': 'daily_local_ai_security_briefing',
}
TASK_TEMPLATE_ALIASES = {
    'topic digest': 'topic_digest',
    'digest': 'topic_digest',
    'briefing': 'topic_digest',
    'brief': 'topic_digest',
    'server health': 'server_health',
    'health check': 'server_health',
    'backup verification': 'backup_verification',
    'backup verifier': 'backup_verification',
    'backup check': 'backup_verification',
    'n8n webhook': 'n8n_webhook',
    'webhook': 'n8n_webhook',
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
        'Try asking to list, show, draft, request approval for, pause, or run approved automation tasks.'
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


def automation_template_id_from_text(text: str) -> str | None:
    lowered = text.lower()
    for phrase, template_id in TASK_TEMPLATE_ALIASES.items():
        if phrase in lowered:
            return template_id
    match = re.search(r'\b(topic_digest|server_health|backup_verification|n8n_webhook)\b', lowered)
    return match.group(1) if match else None


def automation_run_id_from_text(text: str) -> str | None:
    match = RUN_ID_RE.search(text)
    return match.group(1).lower() if match else None


def task_change_proposal_id_from_text(text: str) -> str | None:
    if not re.search(r'\b(proposal|proposals|task change|task changes|change proposal)\b', text, re.IGNORECASE):
        return None
    return automation_run_id_from_text(text)


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
            {
                'source_id': 'open_webui_releases',
                'type': 'rss',
                'url': 'https://github.com/open-webui/open-webui/releases.atom',
            },
            {
                'source_id': 'ollama_releases',
                'type': 'rss',
                'url': 'https://github.com/ollama/ollama/releases.atom',
            },
            {
                'source_id': 'n8n_releases',
                'type': 'rss',
                'url': 'https://github.com/n8n-io/n8n/releases.atom',
            },
            {
                'source_id': 'docker_blog',
                'type': 'rss',
                'url': 'https://www.docker.com/blog/feed/',
            },
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
            'max_runs_per_hour': 3,
            'max_runs_per_day': 10,
            'min_seconds_between_runs': 300,
            'allow_external_side_effects': False,
            'allow_shell': False,
            'allow_docker_socket': False,
            'allow_filesystem_write': False,
        },
        'runtime': {'dry_run': True, 'timeout_seconds': 120, 'retry_count': 1},
        'notifications': {
            'on_success': True,
            'on_failure': True,
            'on_empty_result': False,
            'quiet_hours': {
                'enabled': True,
                'start': '22:00',
                'end': '07:00',
                'timezone': 'Europe/Berlin',
            },
            'collapse_repeated_failures': True,
            'failure_collapse_window_minutes': 360,
        },
    }


def local_ai_security_template_values(text: str) -> dict[str, Any]:
    cron, timezone = parse_schedule(text)
    return {
        'id': 'daily_local_ai_security_briefing',
        'name': 'Daily Local AI Security Briefing',
        'cron': cron,
        'timezone': timezone,
        'output_target': 'briefings',
        'source_ids': [
            'open_webui_releases',
            'ollama_releases',
            'n8n_releases',
            'docker_blog',
        ],
        'include': ['Open WebUI', 'Ollama', 'Hermes', 'Docker', 'n8n', 'local AI security'],
        'exclude': ['sponsored', 'rumor'],
        'max_items': 10,
        'owner': 'local_user',
        'created_by': 'yggdrasil',
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


def format_task_template(template_id: str, template: dict[str, Any]) -> str:
    targets = template.get('allowed_output_targets') or template.get('targets') or []
    targets_text = ', '.join(f'`{target}`' for target in targets)
    approval = template.get('default_approval_level') or template.get('approval_level') or 'n/a'
    purpose = template.get('description') or template.get('purpose') or 'n/a'
    return (
        f"Task template `{template_id}`\n\n"
        f"- Name: {template.get('name')}\n"
        f"- Task type: `{template.get('task_type')}`\n"
        f"- Default approval: `{approval}`\n"
        f"- Allowed targets: {targets_text}\n"
        f"- Purpose: {purpose}\n\n"
        "Rendered tasks are disabled and dry-run by default, then must pass automation API validation. "
        "A template does not approve, enable, or run a task."
    )


def format_task_template_list(templates: list[dict[str, Any]]) -> str:
    if not templates:
        return 'No task templates are registered yet.'
    lines = ['Task templates:']
    for template in templates:
        targets = ', '.join(template.get('allowed_output_targets') or template.get('targets') or [])
        approval = template.get('default_approval_level') or template.get('approval_level') or 'n/a'
        lines.append(
            f"- `{template.get('id')}`: {template.get('name')} "
            f"({approval}, targets: {targets})"
        )
    lines.extend([
        '',
        'Templates are disabled dry-run scaffolds. Use them to draft task YAML, then review and approve through the control plane.',
    ])
    return '\n'.join(lines)


def format_task_change_proposal(proposal: dict[str, Any]) -> str:
    risk = proposal.get('risk') if isinstance(proposal.get('risk'), dict) else {}
    diff = proposal.get('diff') if isinstance(proposal.get('diff'), dict) else {}
    counts = diff.get('counts') if isinstance(diff.get('counts'), dict) else {}
    lines = [
        f"Task change proposal `{proposal.get('id')}`",
        "",
        f"- Task: `{proposal.get('task_id')}`",
        f"- Status: `{proposal.get('status')}`",
        f"- Approval level: `{proposal.get('approval_level')}`",
        f"- Risk: `{risk.get('severity', 'n/a')}`",
        f"- Summary: {proposal.get('summary') or 'n/a'}",
        f"- Diff: `{counts.get('changed', 0)} changed`, `{counts.get('added', 0)} added`, `{counts.get('removed', 0)} removed`",
    ]
    changed = diff.get('changed') if isinstance(diff.get('changed'), list) else []
    if changed:
        lines.extend(["", "Changed paths:"])
        for item in changed[:8]:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('path')}`: `{item.get('before')}` -> `{item.get('after')}`")
    lines.extend([
        "",
        "Approve/apply only through the local admin CLI/UI. I do not have the admin key.",
    ])
    return '\n'.join(lines)


def format_task_change_proposal_list(proposals: list[dict[str, Any]]) -> str:
    if not proposals:
        return 'No task change proposals found.'
    lines = ['Task change proposals:']
    for proposal in proposals:
        risk = proposal.get('risk') if isinstance(proposal.get('risk'), dict) else {}
        lines.append(
            f"- `{proposal.get('id')}` task `{proposal.get('task_id')}` "
            f"status `{proposal.get('status')}`, risk `{risk.get('severity', 'n/a')}`"
        )
    lines.append('')
    lines.append('Approve/apply only through the local admin CLI/UI.')
    return '\n'.join(lines)


def proposal_query_path(text: str) -> str:
    params: dict[str, Any] = {'limit': 20}
    task_id = automation_task_id_from_text(text)
    if task_id:
        params['task_id'] = task_id
    if re.search(r'\b(pending|open|unapproved)\b', text, re.IGNORECASE):
        params['status'] = 'pending'
    if re.search(r'\b(approved)\b', text, re.IGNORECASE):
        params['status'] = 'approved'
    if re.search(r'\b(applied)\b', text, re.IGNORECASE):
        params['status'] = 'applied'
    if re.search(r'\b(rejected|cancelled|canceled)\b', text, re.IGNORECASE):
        params['status'] = 'rejected'
    return f"/task-change-proposals?{urllib.parse.urlencode(params)}"


def propose_schedule_change(user_text: str) -> str:
    task_id = automation_task_id_from_text(user_text)
    if not task_id:
        return 'Which automation task should I change? Give me the task id.'
    cron, timezone = parse_schedule(user_text)
    status_code, task = automation_request('GET', f'/tasks/{task_id}')
    if status_code != 200 or not isinstance(task, dict):
        return f'Automation API returned status `{status_code}` while loading task `{task_id}`:\n\n```json\n{json.dumps(task, indent=2)}\n```'
    config = task.get('config')
    if not isinstance(config, dict):
        return f'Task `{task_id}` did not return a usable config.'
    proposed = json.loads(json.dumps(config))
    proposed.setdefault('trigger', {})
    proposed['trigger']['cron'] = cron
    proposed['trigger']['timezone'] = timezone
    payload = {
        'requested_by': 'yggdrasil',
        'summary': f'Propose schedule change for {task_id} to {cron} {timezone}.',
        'proposed_config': proposed,
    }
    create_status, proposal = automation_request('POST', f'/tasks/{task_id}/propose-change', payload)
    if create_status == 201 and isinstance(proposal, dict):
        return (
            "Task change proposal created.\n\n"
            f"{format_task_change_proposal(proposal)}\n\n"
            f"Nonce: `{proposal.get('nonce')}`\n\n"
            "Keep the nonce local; approve/apply only through the admin CLI/UI."
        )
    return f'Automation API returned status `{create_status}` while creating the task change proposal:\n\n```json\n{json.dumps(proposal, indent=2)}\n```'


def run_delivery_text(run: dict[str, Any]) -> str:
    log = run.get('log') if isinstance(run.get('log'), dict) else {}
    notification = log.get('notification') if isinstance(log.get('notification'), dict) else {}
    if notification:
        if notification.get('sent') is True:
            transport = notification.get('transport') or 'configured transport'
            return f"sent via {transport}"
        if notification.get('dry_run') is True:
            return 'dry-run only; no live Discord message sent'
        return 'notification attempted but not marked sent'
    if str(run.get('status', '')).endswith('dry_run'):
        return 'dry-run only; no live Discord message sent'
    return 'not recorded'


def run_summary_values(run: dict[str, Any]) -> dict[str, Any]:
    log = run.get('log') if isinstance(run.get('log'), dict) else {}
    result = log.get('result') if isinstance(log.get('result'), dict) else {}
    notification = log.get('notification') if isinstance(log.get('notification'), dict) else {}
    items = result.get('items') if isinstance(result.get('items'), list) else []
    errors = result.get('errors') if isinstance(result.get('errors'), list) else []
    source_health = result.get('source_health') if isinstance(result.get('source_health'), list) else []
    healthy_sources = sum(1 for health in source_health if isinstance(health, dict) and health.get('status') == 'ok')
    blocked_sources = sum(1 for health in source_health if isinstance(health, dict) and health.get('status') == 'blocked')
    failed_sources = sum(1 for health in source_health if isinstance(health, dict) and health.get('status') == 'error')
    dry_run = notification.get('dry_run')
    if dry_run is None:
        dry_run = str(run.get('status', '')).endswith('dry_run') or bool(log.get('dry_run', False))
    return {
        'delivery': run_delivery_text(run),
        'dry_run': dry_run,
        'summary_mode': result.get('summary_mode') or 'n/a',
        'summary_error': result.get('summary_error') or 'none',
        'item_count': len(items),
        'source_count': result.get('source_count', 'n/a'),
        'approved_source_count': result.get('approved_source_count', 'n/a'),
        'source_health': source_health,
        'source_health_text': (
            'n/a' if not source_health else f'{healthy_sources} ok, {failed_sources} failed, {blocked_sources} blocked'
        ),
        'errors': errors,
        'failure': log.get('message') or log.get('error') or 'none',
    }


def format_health_run(run: dict[str, Any], result: dict[str, Any]) -> str:
    values = run_summary_values(run)
    checks = result.get('checks') if isinstance(result.get('checks'), list) else []
    ok_count = result.get('ok_count')
    if ok_count is None:
        ok_count = sum(1 for check in checks if isinstance(check, dict) and check.get('ok') is True)
    failed_count = result.get('failed_count')
    if failed_count is None:
        failed_count = sum(1 for check in checks if isinstance(check, dict) and check.get('ok') is not True)
    total_count = len(checks)
    notify = result.get('notify')
    if notify is False:
        delivery = 'alert suppressed; no anomalies detected'
    else:
        delivery = values['delivery']

    lines = [
        f"Run `{run.get('id')}`",
        "",
        f"- Task: `{run.get('task_id')}`",
        f"- Status: `{run.get('status')}`",
        f"- Health: `{result.get('status', 'unknown')}`",
        f"- Delivery: {delivery}",
        f"- Dry run: `{str(values['dry_run']).lower()}`",
        f"- Checks: `{ok_count}/{total_count} ok`, failed `{failed_count}`",
        f"- Created: `{run.get('created_at')}`",
        f"- Completed: `{run.get('completed_at') or 'not completed'}`",
    ]
    if checks:
        lines.extend(["", "Check details:"])
        for check in checks[:8]:
            if not isinstance(check, dict):
                continue
            name = check.get('name') or check.get('type') or 'check'
            status_text = 'ok' if check.get('ok') is True else 'failed'
            details: list[str] = []
            if check.get('status_code') is not None:
                details.append(f"status `{check.get('status_code')}`")
            if check.get('latency_ms') is not None:
                details.append(f"latency `{check.get('latency_ms')}ms`")
            if check.get('worker_age_seconds') is not None:
                details.append(f"worker age `{check.get('worker_age_seconds')}s`")
            if check.get('model_count') is not None:
                details.append(f"models `{check.get('model_count')}`")
            if check.get('metrics_failed_count') is not None:
                details.append(f"metrics failed `{check.get('metrics_failed_count')}`")
            if check.get('metrics_failed_services'):
                details.append(f"failed services: {', '.join(str(item) for item in check.get('metrics_failed_services')[:5])}")
            if check.get('error'):
                details.append(f"error: {check.get('error')}")
            suffix = f" ({', '.join(details)})" if details else ''
            lines.append(f"- `{name}`: {status_text}{suffix}")
    return '\n'.join(lines)


def format_backup_run(run: dict[str, Any], result: dict[str, Any]) -> str:
    values = run_summary_values(run)
    latest = result.get('latest_backup') if isinstance(result.get('latest_backup'), dict) else {}
    restore = result.get('restore_dry_run') if isinstance(result.get('restore_dry_run'), dict) else {}
    secret_scan = result.get('secret_scan') if isinstance(result.get('secret_scan'), dict) else {}
    anomalies = result.get('anomalies') if isinstance(result.get('anomalies'), list) else []
    notify = result.get('notify')
    if notify is False:
        delivery = 'alert suppressed; no anomalies detected'
    else:
        delivery = values['delivery']

    lines = [
        f"Run `{run.get('id')}`",
        "",
        f"- Task: `{run.get('task_id')}`",
        f"- Status: `{run.get('status')}`",
        f"- Backup verification: `{result.get('status', 'unknown')}`",
        f"- Delivery: {delivery}",
        f"- Dry run: `{str(values['dry_run']).lower()}`",
        f"- Backups found: `{result.get('backup_count', 'n/a')}`",
        f"- Latest backup: `{latest.get('name', 'n/a')}`",
        f"- Backup age: `{latest.get('age_hours', 'n/a')}h`",
        f"- MySQL dump bytes: `{latest.get('mysql_dump_bytes', 'n/a')}`",
        f"- Restore dry-run: `{'ok' if restore.get('ok') else 'failed'}`",
        f"- Secret scan: `{secret_scan.get('status', 'n/a')}`",
        f"- Failed checks: `{result.get('failed_count', 0)}`",
        f"- Created: `{run.get('created_at')}`",
        f"- Completed: `{run.get('completed_at') or 'not completed'}`",
    ]
    if anomalies:
        lines.extend(["", "Anomalies:"])
        for anomaly in anomalies[:8]:
            if not isinstance(anomaly, dict):
                continue
            lines.append(f"- `{anomaly.get('check', 'check')}`: {anomaly.get('detail', anomaly.get('status', 'failed'))}")
    return '\n'.join(lines)


def format_run(run: dict[str, Any]) -> str:
    log = run.get('log') if isinstance(run.get('log'), dict) else {}
    result = log.get('result') if isinstance(log.get('result'), dict) else {}
    if isinstance(result.get('latest_backup'), dict) or isinstance(result.get('restore_dry_run'), dict):
        return format_backup_run(run, result)
    if isinstance(result.get('checks'), list):
        return format_health_run(run, result)

    values = run_summary_values(run)
    source_errors = values['errors']
    source_error_text = 'none' if not source_errors else ', '.join(
        f"{error.get('source', 'source')}: {error.get('error', 'error')}" for error in source_errors[:3]
    )
    lines = [
        f"Run `{run.get('id')}`",
        "",
        f"- Task: `{run.get('task_id')}`",
        f"- Status: `{run.get('status')}`",
        f"- Delivery: {values['delivery']}",
        f"- Dry run: `{str(values['dry_run']).lower()}`",
        f"- Summary mode: `{values['summary_mode']}`",
        f"- Summary error: `{values['summary_error']}`",
        f"- Items: `{values['item_count']}`",
        f"- Sources: `{values['source_count']}`",
        f"- Approved sources: `{values['approved_source_count']}`",
        f"- Source health: {values['source_health_text']}",
        f"- Source errors: {source_error_text}",
        f"- Created: `{run.get('created_at')}`",
        f"- Completed: `{run.get('completed_at') or 'not completed'}`",
    ]
    if run.get('status') == 'failed':
        lines.append(f"- Failure: {values['failure']}")
    return '\n'.join(lines)


def format_run_list(runs: list[dict[str, Any]], *, title: str = 'Automation runs') -> str:
    if not runs:
        return f'{title}: none found.'
    lines = [f'{title}:']
    for run in runs:
        values = run_summary_values(run)
        lines.append(
            f"- `{run.get('id')}` `{run.get('status')}` task `{run.get('task_id')}`; "
            f"delivery: {values['delivery']}; completed: `{run.get('completed_at') or 'not completed'}`"
        )
    return '\n'.join(lines)


def query_runs_path(*, task_id: str | None = None, status_value: str | None = None, limit: int = 5) -> str:
    params: dict[str, Any] = {'limit': limit}
    if task_id:
        params = {'task_id': task_id, **params}
    if status_value:
        params = {'status': status_value, **params}
    return f"/runs?{urllib.parse.urlencode(params)}"


def format_draft_response(status_code: int, body: Any, draft: dict[str, Any], *, template_id: str | None = None) -> str:
    if status_code == 201 and isinstance(body, dict):
        task = body.get('task') or {}
        approval = body.get('approval')
        rendered = body.get('rendered_config') if isinstance(body.get('rendered_config'), dict) else draft
        template_text = f" from template `{template_id}`" if template_id else ''
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
            f"Draft task `{task.get('id')}` was created{template_text} and remains disabled.\n\n"
            f"{format_task(task)}"
            f"{approval_text}\n\n"
            "YAML draft:\n"
            f"```yaml\n{to_yaml(rendered)}\n```"
        )
    if status_code == 409:
        task_id = draft['id']
        get_status, existing = automation_request('GET', f'/tasks/{task_id}')
        if get_status == 200 and isinstance(existing, dict):
            draft_label = 'Requested template values' if template_id else 'Current YAML shape for the requested draft'
            return (
                f"Task `{task_id}` already exists, so I did not create a duplicate.\n\n"
                f"{format_task(existing)}\n\n"
                f"{draft_label}:\n"
                f"```yaml\n{to_yaml(draft)}\n```"
            )
    return f'Automation API rejected the draft with status `{status_code}`:\n\n```json\n{json.dumps(body, indent=2)}\n```'


def canonical_task_id(payload: dict[str, Any]) -> str | None:
    task_id = str(payload.get('task_id') or '').strip()
    if not task_id:
        return None
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,127}', task_id):
        raise ValueError('task_id must be slug-like')
    return task_id


def canonical_string_list(value: Any, *, field_name: str, max_items: int = 20, max_length: int = 120) -> list[str]:
    if value is None:
        return []
    raw_items = value.split(',') if isinstance(value, str) else value
    if not isinstance(raw_items, list):
        raise ValueError(f'{field_name} must be a list of strings')
    items: list[str] = []
    for raw in raw_items:
        item = str(raw).strip()
        if not item:
            continue
        if len(item) > max_length:
            raise ValueError(f'{field_name} entries must be {max_length} characters or shorter')
        if re.search(r'(?i)\b(api[_-]?key|token|password|secret|webhook[_-]?url|private[_-]?key|cookie|nonce)\b', item):
            raise ValueError(f'{field_name} contains secret-like material')
        if item.lower() not in {existing.lower() for existing in items}:
            items.append(item)
    if len(items) > max_items:
        raise ValueError(f'{field_name} may contain at most {max_items} entries')
    return items


def canonical_source_id_list(value: Any, *, field_name: str) -> list[str]:
    ids = canonical_string_list(value, field_name=field_name, max_items=20, max_length=128)
    for source_id in ids:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,127}', source_id):
            raise ValueError(f'{field_name} entries must be slug-like approved source IDs')
    return ids


def source_registry_by_id() -> dict[str, dict[str, Any]]:
    status_code, body = automation_request('GET', '/sources')
    if status_code != 200 or not isinstance(body, list):
        raise ValueError(f'could not load approved source registry from automation API: status {status_code}')
    sources: dict[str, dict[str, Any]] = {}
    for item in body:
        if isinstance(item, dict) and item.get('enabled', True) is not False and item.get('id'):
            sources[str(item['id'])] = item
    return sources


def rendered_source_from_registry(source_id: str, registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = registry.get(source_id)
    if source is None:
        raise ValueError(f'source_id `{source_id}` is not enabled in the approved source registry')
    source_type = str(source.get('type') or '').strip()
    rendered = {'source_id': source_id, 'type': source_type}
    if source_type in {'rss', 'http'}:
        url = str(source.get('url') or '').strip()
        if not re.match(r'^https?://', url):
            raise ValueError(f'source_id `{source_id}` does not expose a usable http/https URL')
        rendered['url'] = url
    elif source_type == 'web_query':
        query = str(source.get('query') or '').strip()
        if not query:
            raise ValueError(f'source_id `{source_id}` does not expose a usable query')
        rendered['query'] = query
    else:
        raise ValueError(f'source_id `{source_id}` has unsupported type `{source_type}`')
    return rendered


def source_id_for_config(source: dict[str, Any], registry: dict[str, dict[str, Any]]) -> str | None:
    configured = str(source.get('source_id') or '').strip()
    if configured:
        return configured
    source_type = str(source.get('type') or '').strip()
    url = str(source.get('url') or '').strip()
    query = str(source.get('query') or '').strip()
    for source_id, registered in registry.items():
        if str(registered.get('type') or '').strip() != source_type:
            continue
        if source_type in {'rss', 'http'} and str(registered.get('url') or '').strip() == url:
            return source_id
        if source_type == 'web_query' and str(registered.get('query') or '').strip() == query:
            return source_id
    return None


def merge_unique_strings(existing: list[str], additions: list[str]) -> list[str]:
    merged = [item for item in existing if str(item).strip()]
    lowered = {item.lower() for item in merged}
    for item in additions:
        if item.lower() not in lowered:
            merged.append(item)
            lowered.add(item.lower())
    return merged


def remove_strings(existing: list[str], removals: list[str]) -> list[str]:
    removal_set = {item.lower() for item in removals}
    return [item for item in existing if item.lower() not in removal_set]


def apply_topic_digest_subject_change(config: dict[str, Any], change: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[str]]]:
    if config.get('type') != 'topic_digest':
        raise ValueError('topic digest subject changes can only target topic_digest tasks')
    if 'user_request' in change or 'raw_text' in change:
        raise ValueError('raw natural language is not accepted on the canonical task-change path')

    add_source_ids = canonical_source_id_list(change.get('add_source_ids'), field_name='add_source_ids')
    remove_source_ids = canonical_source_id_list(change.get('remove_source_ids'), field_name='remove_source_ids')
    add_include = canonical_string_list(change.get('add_include'), field_name='add_include')
    remove_include = canonical_string_list(change.get('remove_include'), field_name='remove_include')
    output_target = str(change.get('output_target') or '').strip()

    if not any((add_source_ids, remove_source_ids, add_include, remove_include, output_target)):
        raise ValueError('at least one topic digest subject change is required')

    proposed = json.loads(json.dumps(config))
    registry = source_registry_by_id() if add_source_ids or remove_source_ids else {}

    if add_source_ids or remove_source_ids:
        sources = proposed.setdefault('sources', [])
        if not isinstance(sources, list):
            raise ValueError('task config sources must be a list')
        if remove_source_ids:
            remove_set = set(remove_source_ids)
            sources[:] = [
                source
                for source in sources
                if not (isinstance(source, dict) and source_id_for_config(source, registry) in remove_set)
            ]
        existing_after_remove = {
            source_id_for_config(source, registry)
            for source in sources
            if isinstance(source, dict)
        }
        for source_id in add_source_ids:
            if source_id not in existing_after_remove:
                sources.append(rendered_source_from_registry(source_id, registry))
                existing_after_remove.add(source_id)
        if not sources:
            raise ValueError('topic digest must keep at least one source')

    if add_include or remove_include:
        filters = proposed.setdefault('filters', {})
        if not isinstance(filters, dict):
            raise ValueError('task config filters must be an object')
        include = filters.setdefault('include', [])
        if not isinstance(include, list):
            raise ValueError('task config filters.include must be a list')
        include_values = [str(item).strip() for item in include if str(item).strip()]
        include_values = merge_unique_strings(include_values, add_include)
        include_values = remove_strings(include_values, remove_include)
        filters['include'] = include_values

    if output_target:
        if output_target not in {'briefings', 'alerts'}:
            raise ValueError('output_target must be briefings or alerts')
        output = proposed.setdefault('output', {})
        if not isinstance(output, dict):
            raise ValueError('task config output must be an object')
        output['target'] = output_target

    applied = {
        'add_source_ids': add_source_ids,
        'remove_source_ids': remove_source_ids,
        'add_include': add_include,
        'remove_include': remove_include,
    }
    if output_target:
        applied['output_target'] = [output_target]
    return proposed, applied


def strip_nonce(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_nonce(item) for key, item in value.items() if str(key).lower() != 'nonce'}
    if isinstance(value, list):
        return [strip_nonce(item) for item in value]
    return value


def format_subject_change_response(status_code: int, body: Any, task_id: str) -> str:
    if status_code == 201 and isinstance(body, dict):
        proposal = strip_nonce(body)
        return (
            "Task change proposal created for the existing topic digest.\n\n"
            f"{format_task_change_proposal(proposal)}\n\n"
            "The approval nonce is intentionally not shown on this model-facing path. "
            "Use the local /ops UI or admin CLI to review, approve, and apply it."
        )
    return f'Automation API returned status `{status_code}` while creating a task change proposal for `{task_id}`:\n\n```json\n{json.dumps(strip_nonce(body), indent=2)}\n```'


def handle_canonical_action(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get('action') or '').strip()
    capability_id = str(payload.get('capability_id') or '').strip()
    template_id = str(payload.get('template_id') or '').strip()
    template_values = payload.get('template_values')
    if action == 'list_tasks':
        status_code, body = automation_request('GET', '/tasks')
        answer = format_task_list(body) if status_code == 200 and isinstance(body, list) else (
            f'Automation API returned status `{status_code}` while listing tasks:\n\n```json\n{json.dumps(body, indent=2)}\n```'
        )
        return 200, {'status': 'ok' if status_code == 200 else 'automation_api_rejected', 'automation_api_status': status_code, 'automation_api_body': body, 'answer': answer}
    if action == 'show_task':
        try:
            task_id = canonical_task_id(payload)
        except ValueError as exc:
            return 422, {'status': 'rejected', 'detail': str(exc)}
        if not task_id:
            return 422, {'status': 'rejected', 'detail': 'task_id is required'}
        status_code, body = automation_request('GET', f'/tasks/{task_id}')
        answer = format_task(body) if status_code == 200 and isinstance(body, dict) else (
            f'Automation API returned status `{status_code}` for task `{task_id}`:\n\n```json\n{json.dumps(body, indent=2)}\n```'
        )
        return 200, {'status': 'ok' if status_code == 200 else 'automation_api_rejected', 'task_id': task_id, 'automation_api_status': status_code, 'automation_api_body': body, 'answer': answer}
    if action == 'run_task':
        try:
            task_id = canonical_task_id(payload)
        except ValueError as exc:
            return 422, {'status': 'rejected', 'detail': str(exc)}
        if not task_id:
            return 422, {'status': 'rejected', 'detail': 'task_id is required'}
        status_code, body = automation_request('POST', f'/tasks/{task_id}/run')
        if status_code in {200, 202} and isinstance(body, dict):
            if body.get('deduplicated'):
                lines = [f"Run not queued for task `{task_id}` because `{body.get('reason', body.get('status', 'deduplicated'))}`."]
                if body.get('run_id'):
                    lines.append(f"Existing run: `{body.get('run_id')}`")
                if body.get('retry_after_seconds') is not None:
                    lines.append(f"Retry after: `{body.get('retry_after_seconds')}s`")
                answer = '\n\n'.join(lines) + f"\n\n```json\n{json.dumps(body, indent=2)}\n```"
            else:
                answer = f"Run queued for task `{task_id}`.\n\n```json\n{json.dumps(body, indent=2)}\n```"
        else:
            answer = f'Automation API returned status `{status_code}` while queueing the run:\n\n```json\n{json.dumps(body, indent=2)}\n```'
        return 200, {'status': 'ok' if status_code in {200, 202} else 'automation_api_rejected', 'task_id': task_id, 'automation_api_status': status_code, 'automation_api_body': body, 'answer': answer}
    if action == 'pause_task':
        try:
            task_id = canonical_task_id(payload)
        except ValueError as exc:
            return 422, {'status': 'rejected', 'detail': str(exc)}
        if not task_id:
            return 422, {'status': 'rejected', 'detail': 'task_id is required'}
        status_code, body = automation_request('POST', f'/tasks/{task_id}/pause')
        answer = f"Task `{task_id}` is paused.\n\n{format_task(body)}" if status_code == 200 and isinstance(body, dict) else (
            f'Automation API returned status `{status_code}` while pausing the task:\n\n```json\n{json.dumps(body, indent=2)}\n```'
        )
        return 200, {'status': 'ok' if status_code == 200 else 'automation_api_rejected', 'task_id': task_id, 'automation_api_status': status_code, 'automation_api_body': body, 'answer': answer}
    if action == 'propose_task_change':
        try:
            task_id = canonical_task_id(payload)
        except ValueError as exc:
            return 422, {'status': 'rejected', 'detail': str(exc)}
        if not task_id:
            return 422, {'status': 'rejected', 'detail': 'task_id is required'}
        if capability_id != 'topic_digest.modify_subjects.v1':
            return 422, {'status': 'rejected', 'detail': 'unsupported task-change capability_id'}
        if payload.get('change_type') != 'topic_digest_subjects':
            return 422, {'status': 'rejected', 'detail': 'unsupported change_type'}
        change = payload.get('change')
        if not isinstance(change, dict):
            return 422, {'status': 'rejected', 'detail': 'change must be an object'}
        status_code, task = automation_request('GET', f'/tasks/{task_id}')
        if status_code != 200 or not isinstance(task, dict):
            answer = f'Automation API returned status `{status_code}` while loading task `{task_id}`:\n\n```json\n{json.dumps(strip_nonce(task), indent=2)}\n```'
            return 200, {'status': 'automation_api_rejected', 'task_id': task_id, 'automation_api_status': status_code, 'automation_api_body': strip_nonce(task), 'answer': answer}
        config = task.get('config')
        if not isinstance(config, dict):
            return 422, {'status': 'rejected', 'detail': f'task `{task_id}` did not return a usable config'}
        try:
            proposed, applied = apply_topic_digest_subject_change(config, change)
        except ValueError as exc:
            return 422, {'status': 'rejected', 'detail': str(exc)}
        summary_parts = []
        if applied.get('add_source_ids'):
            summary_parts.append('add sources ' + ', '.join(applied['add_source_ids']))
        if applied.get('remove_source_ids'):
            summary_parts.append('remove sources ' + ', '.join(applied['remove_source_ids']))
        if applied.get('add_include'):
            summary_parts.append('add include terms ' + ', '.join(applied['add_include']))
        if applied.get('remove_include'):
            summary_parts.append('remove include terms ' + ', '.join(applied['remove_include']))
        if applied.get('output_target'):
            summary_parts.append('set output target ' + ', '.join(applied['output_target']))
        summary = f"Propose topic digest subject change for {task_id}: {'; '.join(summary_parts)}."
        create_status, proposal = automation_request(
            'POST',
            f'/tasks/{task_id}/propose-change',
            {'requested_by': 'yggdrasil', 'summary': summary[:1200], 'proposed_config': proposed},
        )
        answer = format_subject_change_response(create_status, proposal, task_id)
        return 200, {
            'status': 'ok' if create_status == 201 else 'automation_api_rejected',
            'capability_id': capability_id,
            'task_id': task_id,
            'automation_api_status': create_status,
            'automation_api_body': strip_nonce(proposal),
            'answer': answer,
        }
    if action != 'draft_task_from_template':
        return 422, {'status': 'rejected', 'detail': 'unsupported canonical action'}
    if template_id not in {'server_health', 'topic_digest', 'n8n_webhook'}:
        return 422, {'status': 'rejected', 'detail': 'unsupported template_id'}
    if not re.fullmatch(r'[a-z][a-z0-9_]*\.v[0-9]+', capability_id):
        return 422, {'status': 'rejected', 'detail': 'invalid capability_id'}
    if not isinstance(template_values, dict):
        return 422, {'status': 'rejected', 'detail': 'template_values must be an object'}
    if 'user_request' in template_values or 'raw_text' in template_values:
        return 422, {'status': 'rejected', 'detail': 'raw natural language is not accepted on the canonical action path'}
    status_code, body = automation_request('POST', f'/task-templates/{template_id}/draft', template_values)
    answer = format_draft_response(status_code, body, template_values, template_id=template_id)
    return 200, {
        'status': 'ok' if status_code == 201 else 'automation_api_rejected',
        'capability_id': capability_id,
        'template_id': template_id,
        'automation_api_status': status_code,
        'automation_api_body': body,
        'answer': answer,
    }


def handle_automation_request(user_text: str) -> str | None:
    lowered = user_text.lower()
    automation_words = ('automation', 'automations', 'task', 'tasks', 'run', 'runs', 'control plane', 'template', 'templates')
    project_words = (
        'daily brief',
        'daily briefing',
        'daily security brief',
        'daily security briefing',
        'local ai brief',
        'local ai briefing',
        'local ai security briefing',
        'daily local ai security briefing',
        'server health',
        'server health check',
        'morning server health',
        'morning server health check',
        'backup',
        'backups',
        'backup check',
        'backup verification',
        'backup health',
        'open webui',
        'ollama',
        'yggdrasil',
    )
    requested_run_id = automation_run_id_from_text(user_text)
    if not requested_run_id and not any(word in lowered for word in automation_words + project_words):
        return None

    proposal_id = task_change_proposal_id_from_text(user_text)
    if proposal_id and re.search(r'\b(show|get|inspect|details?|status)\b', lowered):
        status_code, body = automation_request('GET', f'/task-change-proposals/{proposal_id}')
        if status_code == 200 and isinstance(body, dict):
            return format_task_change_proposal(body)
        return f'Automation API returned status `{status_code}` for task change proposal `{proposal_id}`:\n\n```json\n{json.dumps(body, indent=2)}\n```'

    if re.search(r'\b(task changes?|change proposals?|task change proposals?|proposals?)\b', lowered) and re.search(
        r'\b(list|show|pending|open|recent|approved|applied|rejected|status)\b',
        lowered,
    ):
        status_code, body = automation_request('GET', proposal_query_path(user_text))
        if status_code == 200 and isinstance(body, list):
            return format_task_change_proposal_list(body)
        return f'Automation API returned status `{status_code}` while listing task change proposals:\n\n```json\n{json.dumps(body, indent=2)}\n```'

    if re.search(r'\b(change|update|move|set|modify)\b', lowered) and re.search(r'\b(schedule|cron|time|[012]?\d:[0-5]\d)\b', lowered):
        return propose_schedule_change(user_text)

    if re.search(r'\b(template|templates|scaffold|scaffolds)\b', lowered):
        template_id = automation_template_id_from_text(user_text)
        if template_id and re.search(r'\b(show|get|inspect|details?|describe|explain)\b', lowered):
            status_code, body = automation_request('GET', f'/task-templates/{template_id}')
            if status_code == 200 and isinstance(body, dict):
                return format_task_template(template_id, body)
            return f'Automation API returned status `{status_code}` while showing template `{template_id}`:\n\n```json\n{json.dumps(body, indent=2)}\n```'
        status_code, body = automation_request('GET', '/task-templates')
        if status_code == 200 and isinstance(body, list):
            return format_task_template_list(body)
        return f'Automation API returned status `{status_code}` while listing task templates:\n\n```json\n{json.dumps(body, indent=2)}\n```'

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
        draft = local_ai_security_template_values(user_text)
        status_code, body = automation_request('POST', '/task-templates/topic_digest/draft', draft)
        return format_draft_response(status_code, body, draft, template_id='topic_digest')

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

    if requested_run_id:
        status_code, body = automation_request('GET', f'/runs/{requested_run_id}')
        if status_code == 200 and isinstance(body, dict):
            return format_run(body)
        return f'Automation API returned status `{status_code}` for run `{requested_run_id}`:\n\n```json\n{json.dumps(body, indent=2)}\n```'

    server_health_status_request = bool(
        re.search(r'\b(show|get|inspect|status|latest|last)\b', lowered)
        and re.search(r'\b(server health|health check|morning server health)\b', lowered)
        and 'task' not in lowered
    )
    backup_status_request = bool(
        re.search(r'\b(show|get|inspect|status|latest|last)\b', lowered)
        and re.search(r'\b(backup|backups|backup check|backup verification|backup health)\b', lowered)
        and 'task' not in lowered
    )
    explicit_run_request = bool(re.search(r'^\s*(run|execute|dry run|send|deliver|post|generate)\b', lowered))
    run_status_request = (
        re.search(r'\b(latest|last|recent|failed|failures?|runs?)\b', lowered)
        or re.search(r'\bdid\b.*\bsend\b', lowered)
        or re.search(r'\b(sent|delivery|deliver(?:ed)?)\b', lowered)
        or server_health_status_request
        or backup_status_request
    )
    if run_status_request and not explicit_run_request and (
        re.search(r'\b(run|runs|sent|send|delivery|delivered|failed|failure)\b', lowered)
        or server_health_status_request
        or backup_status_request
    ):
        task_id = automation_task_id_from_text(user_text)
        if re.search(r'\b(failed|failures?)\b', lowered):
            path = query_runs_path(task_id=task_id, status_value='failed', limit=5)
            title = 'Failed automation runs' if task_id is None else f'Failed runs for `{task_id}`'
            status_code, body = automation_request('GET', path)
            if status_code == 200 and isinstance(body, list):
                return format_run_list(body, title=title)
            return f'Automation API returned status `{status_code}` while listing failed runs:\n\n```json\n{json.dumps(body, indent=2)}\n```'

        limit = 1 if server_health_status_request or backup_status_request or re.search(r'\b(latest|last|did\b.*\bsend|sent|delivery|delivered)\b', lowered) else 5
        path = query_runs_path(task_id=task_id, limit=limit)
        status_code, body = automation_request('GET', path)
        if status_code == 200 and isinstance(body, list):
            if not body:
                suffix = f' for `{task_id}`' if task_id else ''
                return f'No automation runs found{suffix}.'
            if limit == 1:
                return format_run(body[0])
            return format_run_list(body, title='Recent automation runs')
        return f'Automation API returned status `{status_code}` while listing runs:\n\n```json\n{json.dumps(body, indent=2)}\n```'

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

    if re.search(r'\b(run|execute|dry run|send|deliver|post|generate)\b', lowered):
        task_id = automation_task_id_from_text(user_text)
        if not task_id:
            return 'Which automation task should I run? Give me the task id.'
        status_code, body = automation_request('POST', f'/tasks/{task_id}/run')
        if status_code in {200, 202}:
            if isinstance(body, dict) and body.get('deduplicated'):
                lines = [
                    f"Run not queued for task `{task_id}` because `{body.get('reason', body.get('status', 'deduplicated'))}`.",
                ]
                if body.get('run_id'):
                    lines.append(f"Existing run: `{body.get('run_id')}`")
                if body.get('retry_after_seconds') is not None:
                    lines.append(f"Retry after: `{body.get('retry_after_seconds')}s`")
                return '\n\n'.join(lines) + f"\n\n```json\n{json.dumps(body, indent=2)}\n```"
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
        'Ask me to list templates, list tasks, show, draft, request approval for, pause, or run approved automation tasks.'
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
        'Ask me to list templates, list tasks, show, draft, request approval for, pause, or run approved automation tasks.'
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
        if self.path == '/v1/yggdrasil/canonical-actions':
            if not self.authorized():
                json_response(self, 401, {'error': {'message': 'unauthorized'}})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('canonical action payload must be an object')
                status_code, response_payload = handle_canonical_action(payload)
                json_response(self, status_code, response_payload)
            except Exception as exc:
                json_response(self, 500, {'error': {'message': str(exc), 'type': exc.__class__.__name__}})
            return
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
