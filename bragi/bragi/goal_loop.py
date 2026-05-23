from __future__ import annotations

import copy
import re
from typing import Any

from .goal_models import (
    GOAL_CAPABILITY_IDS,
    AutomationRequestClassification,
    AutomationRequestKind,
    AutomationTargetKind,
    TaskResolution,
)
from .hermes_client import HermesClarifierClient, HermesClarifierError


TASK_ID_RE = re.compile(r"\b([a-z][a-z0-9_]{2,127})\b")
NON_TASK_ID_PREFIXES = (
    "bragi_intake_",
    "capability_proposal_",
    "task_change_proposal_",
    "approval_",
    "run_",
)

UNSAFE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bapproval nonce\b|\bnonce\b", "approval nonces are not available to Bragi"),
    (r"\badmin(?:\s+api)?\s+key\b|\bapi key\b|\btoken\b|\bpassword\b|\bprivate key\b|\bcookie\b", "secrets and admin credentials are forbidden"),
    (r"\bapprove\b|\breject\b|\bgenehmig|\bablehnen\b", "approval decisions must stay in the local admin path"),
    (r"\bdocker socket\b|\bdocker exec\b|\b/var/run/docker\.sock\b", "Docker socket and Docker exec access are forbidden"),
    (r"\brestart docker\b|\bstart docker\b|\brestart.*services?\b|\bstarte .*dienste neu\b|\bdienste neu\b", "restart docker or service restarts are host administration"),
    (
        r"\bshell\b|\bterminal\b|\bcommand line\b|\brun(?:ning)?\s+(?:a\s+)?command\b|\bexecute(?:\s+a|\s+the)?\s+command\b|\bbash\b|\bpowershell\b",
        "shell execution is forbidden",
    ),
    (r"\bfirewall\b|\biptables\b|\bufw\b|\brouter\b", "firewall or router changes are security-sensitive"),
    (r"\bdelete files?\b|\breorganize files?\b|\blösche\b|\bloesche\b|\bdateien.*(löschen|loeschen|umorganisieren)\b", "broad filesystem mutation is forbidden"),
    (r"\binstall(?:iere)? .*updates?\b|\bautomatic(?:ally)? updates?\b|\bauto(?:matically)? update\b", "automatic updates are not a model-facing action"),
    (r"\brotate credentials?\b|\brotate keys?\b|\bcredential rotation\b", "credential rotation is security-sensitive and not model-facing"),
    (r"\bwebhook url\b|https?://[^ ]*webhook", "raw webhook URLs are forbidden"),
    (r"https?://", "arbitrary URLs are not accepted for executable automations"),
    (r"\bpurchase\b|\bbuy\b|\bkaufen\b", "financial transactions are forbidden"),
)

LIST_PATTERNS = (
    r"\blist\b.*\b(tasks?|automations?|workflows?)\b",
    r"\bshow all\b.*\b(tasks?|automations?|workflows?)\b",
    r"\bwhat\b.*\b(tasks?|automations?|workflows?)\b",
    r"\bliste\b.*\b(aufgaben|automationen|workflows)\b",
    r"\bzeige\b.*\b(alle|meine)\b.*\b(aufgaben|automationen|workflows)\b",
    r"\bwelche\b.*\b(aufgaben|automationen|workflows)\b",
)
INSPECT_VERBS = r"(show|get|inspect|status|details?|view|zeig|zeige|anzeigen)"
RUN_VERBS = r"(run|execute|dry run|send|deliver|generate|start|schick|schicke|sende|ausfuehr|ausführ|starte)"
PAUSE_VERBS = r"(pause|disable|stop|pausiere|deaktiviere|stoppe|halte\s+an)"
MODIFY_VERBS = r"(add|include|cover|remove|drop|exclude|change|modify|update|improve|make|nimm|nehme|füge|fuege|entferne|ändere|aendere|mach)"
CREATE_VERBS = r"(draft|create|set up|setup|schedule|build|prepare|monitor|watch|keep an eye|check|erstelle|richte|plane|überwache|ueberwache|beobachte|prüfe|pruefe)"

SUPPORTED_OPERATION_ACTIONS = {"list_tasks", "show_task", "run_task", "pause_task"}


def classify_automation_request(
    user_text: str,
    *,
    visible_tasks: list[dict[str, Any]] | None = None,
    task_aliases: dict[str, str] | None = None,
    max_candidates: int = 5,
    hermes_client: HermesClarifierClient | None = None,
    use_hermes: bool = False,
    capability_ids: set[str] | None = None,
) -> AutomationRequestClassification:
    deterministic = classify_deterministic_automation_request(
        user_text,
        visible_tasks=visible_tasks,
        task_aliases=task_aliases,
        max_candidates=max_candidates,
    )
    if not use_hermes or hermes_client is None or deterministic.request_kind == AutomationRequestKind.UNSAFE:
        return deterministic

    try:
        advisory = hermes_client.classify_request(
            user_text=user_text,
            visible_tasks=visible_tasks or [],
            task_aliases=task_aliases or {},
            capability_ids=sorted(capability_ids or GOAL_CAPABILITY_IDS),
            deterministic_classification=deterministic,
        )
    except (HermesClarifierError, Exception):
        return deterministic
    return merge_hermes_advisory(
        deterministic,
        advisory,
        visible_tasks=visible_tasks or [],
        task_aliases=task_aliases or {},
    )


def classify_deterministic_automation_request(
    user_text: str,
    *,
    visible_tasks: list[dict[str, Any]] | None = None,
    task_aliases: dict[str, str] | None = None,
    max_candidates: int = 5,
) -> AutomationRequestClassification:
    text = str(user_text or "").strip()
    lowered = text.lower()
    if not text:
        return AutomationRequestClassification(request_kind=AutomationRequestKind.CHAT, confidence=1.0, reason="empty request")

    unsafe_reasons = unsafe_reasons_for_text(text)
    if unsafe_reasons:
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.UNSAFE,
            target_kind=AutomationTargetKind.UNKNOWN,
            confidence=0.96,
            unsafe_reasons=unsafe_reasons,
            reason="request contains forbidden operation or credential material",
        )

    if matches_any(lowered, LIST_PATTERNS):
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.LIST_EXISTING,
            target_kind=AutomationTargetKind.EXISTING_TASK,
            operation={"action": "list_tasks"},
            confidence=0.94,
            reason="request asks to list existing automations",
        )

    if is_help_or_chat_question(text):
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.HELP,
            confidence=0.90,
            reason="help, explanation, or ordinary chat request",
        )

    resolution = resolve_task(
        text,
        visible_tasks=visible_tasks,
        task_aliases=task_aliases,
        max_candidates=max_candidates,
    )
    target_task_id = resolution.task_id if resolution.status == "single" else None
    candidates = [str(candidate.get("id")) for candidate in resolution.candidates if candidate.get("id")]

    if looks_like_new_task_request(lowered):
        capability_id = infer_registered_capability(text)
        if capability_id:
            return AutomationRequestClassification(
                request_kind=AutomationRequestKind.CREATE_NEW,
                target_kind=AutomationTargetKind.NEW_TASK,
                capability_id=capability_id,
                operation={"intent": "draft_task", "capability_id": capability_id},
                confidence=0.86,
                reason="request explicitly creates or sets up a new automation from a registered capability",
            )
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.PROPOSE_NEW_CAPABILITY,
            target_kind=AutomationTargetKind.NEW_CAPABILITY,
            confidence=0.64,
            reason="new automation request does not map to a registered capability",
        )

    existing_kind = existing_operation_kind(lowered)
    if (
        existing_kind == AutomationRequestKind.INSPECT_EXISTING
        and not target_task_id
        and not candidates
        and re.search(r"\b(track|monitor|watch|alert|notify|remind)\b", lowered)
    ):
        existing_kind = None
    if existing_kind == AutomationRequestKind.RUN_EXISTING and not target_task_id and not candidates and looks_like_useful_unsupported_automation(lowered):
        existing_kind = None
    if existing_kind == AutomationRequestKind.RUN_EXISTING and not target_task_id and len(resolution.candidates) > 1:
        enabled_candidates = [
            candidate
            for candidate in resolution.candidates
            if candidate.get("enabled") is True or str(candidate.get("status") or "").lower() == "enabled"
        ]
        if len(enabled_candidates) == 1:
            target_task_id = str(enabled_candidates[0].get("id"))
            candidates = [target_task_id]
    if existing_kind in {
        AutomationRequestKind.INSPECT_EXISTING,
        AutomationRequestKind.RUN_EXISTING,
        AutomationRequestKind.PAUSE_EXISTING,
    }:
        if target_task_id:
            action = {
                AutomationRequestKind.INSPECT_EXISTING: "show_task",
                AutomationRequestKind.RUN_EXISTING: "run_task",
                AutomationRequestKind.PAUSE_EXISTING: "pause_task",
            }[existing_kind]
            return AutomationRequestClassification(
                request_kind=existing_kind,
                target_kind=AutomationTargetKind.EXISTING_TASK,
                target_task_id=target_task_id,
                target_task_candidates=candidates,
                operation={"action": action, "task_id": target_task_id},
                confidence=0.92,
                reason="request maps to a deterministic existing-task operation",
                assumptions=["selected the only enabled matching automation"] if len(resolution.candidates) > 1 else [],
            )
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.NEEDS_CLARIFICATION,
            target_kind=AutomationTargetKind.EXISTING_TASK,
            target_task_candidates=candidates,
            confidence=0.72 if candidates else 0.56,
            missing_information=["target_task_id"],
            reason="existing-task operation is missing an unambiguous task target",
        )

    if looks_like_existing_modification(lowered):
        missing: list[str] = []
        if not target_task_id:
            missing.append("target_task_id")
        if not has_specific_change(text):
            missing.append("change_type")
        if resolution.status == "multiple":
            return AutomationRequestClassification(
                request_kind=AutomationRequestKind.NEEDS_CLARIFICATION,
                target_kind=AutomationTargetKind.EXISTING_TASK,
                target_task_candidates=candidates,
                confidence=0.72,
                missing_information=missing or ["target_task_id"],
                reason="multiple existing task targets match the requested change",
            )
        if missing:
            return AutomationRequestClassification(
                request_kind=AutomationRequestKind.NEEDS_CLARIFICATION,
                target_kind=AutomationTargetKind.EXISTING_TASK,
                target_task_id=target_task_id,
                target_task_candidates=candidates,
                confidence=0.68,
                missing_information=missing,
                reason="existing automation change is underspecified",
            )
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.MODIFY_EXISTING,
            target_kind=AutomationTargetKind.EXISTING_TASK,
            target_task_id=target_task_id,
            target_task_candidates=candidates,
            capability_id="topic_digest.modify_subjects.v1",
            operation={"intent": "propose_task_change", "task_id": target_task_id},
            confidence=0.87,
            reason="request changes an existing automation through a task-change proposal",
        )

    if looks_like_create_request(lowered):
        capability_id = infer_registered_capability(text)
        if capability_id:
            return AutomationRequestClassification(
                request_kind=AutomationRequestKind.CREATE_NEW,
                target_kind=AutomationTargetKind.NEW_TASK,
                capability_id=capability_id,
                operation={"intent": "draft_task", "capability_id": capability_id},
                confidence=0.84,
                reason="request creates a new automation from a registered capability",
            )
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.PROPOSE_NEW_CAPABILITY,
            target_kind=AutomationTargetKind.NEW_CAPABILITY,
            confidence=0.62,
            reason="automation-shaped request does not map to a registered capability",
        )

    if looks_like_useful_unsupported_automation(lowered):
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.PROPOSE_NEW_CAPABILITY,
            target_kind=AutomationTargetKind.NEW_CAPABILITY,
            confidence=0.58,
            reason="useful automation idea needs a new non-executable capability proposal",
        )

    return AutomationRequestClassification(
        request_kind=AutomationRequestKind.CHAT,
        confidence=0.82,
        reason="no deterministic automation goal detected",
    )


def merge_hermes_advisory(
    deterministic: AutomationRequestClassification,
    advisory: AutomationRequestClassification,
    *,
    visible_tasks: list[dict[str, Any]],
    task_aliases: dict[str, str],
) -> AutomationRequestClassification:
    if advisory.request_kind == AutomationRequestKind.UNSAFE:
        return advisory
    if advisory.request_kind == deterministic.request_kind and advisory.candidate_intent:
        cleaned = sanitize_advisory(advisory, visible_tasks=visible_tasks, task_aliases=task_aliases)
        if cleaned is not None:
            return cleaned
    if deterministic.request_kind not in {
        AutomationRequestKind.CHAT,
        AutomationRequestKind.HELP,
        AutomationRequestKind.NEEDS_CLARIFICATION,
        AutomationRequestKind.PROPOSE_NEW_CAPABILITY,
    } and deterministic.confidence >= 0.80:
        return deterministic
    cleaned = sanitize_advisory(advisory, visible_tasks=visible_tasks, task_aliases=task_aliases)
    if cleaned is None:
        return deterministic
    if cleaned.request_kind in {AutomationRequestKind.CHAT, AutomationRequestKind.HELP} and deterministic.request_kind not in {
        AutomationRequestKind.CHAT,
        AutomationRequestKind.HELP,
    }:
        return deterministic
    return cleaned


def sanitize_advisory(
    advisory: AutomationRequestClassification,
    *,
    visible_tasks: list[dict[str, Any]],
    task_aliases: dict[str, str],
) -> AutomationRequestClassification | None:
    cleaned = advisory.model_copy(deep=True)
    if cleaned.operation is not None and not safe_operation(cleaned.operation, visible_tasks=visible_tasks, task_aliases=task_aliases):
        cleaned.operation = None
        if cleaned.request_kind in {
            AutomationRequestKind.INSPECT_EXISTING,
            AutomationRequestKind.RUN_EXISTING,
            AutomationRequestKind.PAUSE_EXISTING,
        }:
            cleaned.request_kind = AutomationRequestKind.NEEDS_CLARIFICATION
            cleaned.missing_information = ["target_task_id"]
            cleaned.reason = "Hermes suggested an existing-task operation without an allowed task target"
    if cleaned.candidate_intent is not None:
        intent = sanitize_candidate_intent(cleaned.candidate_intent)
        cleaned.candidate_intent = intent
        if intent is None and cleaned.request_kind in {AutomationRequestKind.CREATE_NEW, AutomationRequestKind.MODIFY_EXISTING}:
            return None
    return cleaned


def safe_operation(operation: dict[str, Any], *, visible_tasks: list[dict[str, Any]], task_aliases: dict[str, str]) -> bool:
    action = str(operation.get("action") or "")
    if action not in SUPPORTED_OPERATION_ACTIONS:
        return False
    if action == "list_tasks":
        return set(operation) == {"action"}
    task_id = str(operation.get("task_id") or "")
    if not re.match(r"^[a-z][a-z0-9_]{2,127}$", task_id):
        return False
    visible_ids = {str(task.get("id")) for task in visible_tasks if isinstance(task, dict) and task.get("id")}
    alias_ids = {str(task_id) for task_id in task_aliases.values()}
    if visible_ids and task_id not in visible_ids and task_id not in alias_ids:
        return False
    return True


def sanitize_candidate_intent(intent: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(intent, dict):
        return None
    candidate = copy.deepcopy(intent)
    if candidate.get("intent") not in {"draft_task", "propose_task_change"}:
        return None
    if not isinstance(candidate.get("capability_id"), str) or not candidate["capability_id"].strip():
        return None
    slots = candidate.get("slots")
    if not isinstance(slots, dict):
        candidate["slots"] = {}
    candidate["requires_user_confirmation"] = True
    candidate["user_confirmation_obtained"] = False
    candidate.setdefault("confidence", 0.70)
    return candidate


def resolve_task(
    user_text: str,
    visible_tasks: list[dict[str, Any]] | None = None,
    task_aliases: dict[str, str] | None = None,
    max_candidates: int = 5,
) -> TaskResolution:
    text = str(user_text or "")
    lowered = text.lower()
    max_candidates = max(1, max_candidates)
    visible_by_id = {
        str(task.get("id")): task
        for task in (visible_tasks or [])
        if isinstance(task, dict) and task.get("id")
    }

    for match in TASK_ID_RE.finditer(lowered):
        token = match.group(1)
        if "_" not in token:
            continue
        if token.startswith(NON_TASK_ID_PREFIXES):
            continue
        if visible_by_id and token not in visible_by_id:
            continue
        return TaskResolution(
            status="single",
            task_id=token,
            candidates=[task_candidate(token, visible_by_id.get(token))],
            reason="explicit slug-like task ID",
        )

    candidates: list[dict[str, Any]] = []

    def add(task_id: str | None, task: dict[str, Any] | None = None, *, reason: str = "") -> None:
        if not task_id or any(candidate.get("id") == task_id for candidate in candidates):
            return
        candidate = task_candidate(task_id, task or visible_by_id.get(task_id))
        if reason:
            candidate["match_reason"] = reason
        candidates.append(candidate)

    normalized_text = normalize_match_text(text)
    for task in visible_by_id.values():
        task_id = str(task.get("id") or "")
        normalized_id = normalize_match_text(task_id.replace("_", " "))
        normalized_name = normalize_match_text(str(task.get("name") or ""))
        if normalized_id and normalized_id in normalized_text:
            add(task_id, task, reason="visible_task_id")
            continue
        if normalized_name and normalized_name in normalized_text:
            add(task_id, task, reason="visible_task_name")
            continue
        if normalized_text and normalized_name and normalized_text in normalized_name and len(normalized_text) >= 8:
            add(task_id, task, reason="visible_task_name_substring")
            continue

    visible_limited = candidates[:max_candidates]
    if len(visible_limited) == 1:
        return TaskResolution(status="single", task_id=str(visible_limited[0]["id"]), candidates=visible_limited, reason="one exact visible matching task")
    if len(visible_limited) > 1:
        return TaskResolution(status="multiple", candidates=visible_limited, reason="multiple exact visible matching tasks")

    significant_text_terms = significant_task_terms(normalized_text)
    for task in visible_by_id.values():
        task_id = str(task.get("id") or "")
        normalized_id = normalize_match_text(task_id.replace("_", " "))
        normalized_name = normalize_match_text(str(task.get("name") or ""))
        normalized_type = normalize_match_text(str(task.get("type") or ""))
        haystack = " ".join(part for part in (normalized_id, normalized_name, normalized_type) if part)
        if significant_text_terms and all(term in haystack for term in significant_text_terms):
            add(task_id, task, reason="visible_task_terms")
            continue
        if "brief" in significant_text_terms and re.search(r"\b(brief|briefing|digest)\b", haystack):
            add(task_id, task, reason="brief_like_task")
            continue
        if {"health", "check"} <= set(significant_text_terms) and re.search(r"\bhealth\b.*\bcheck\b|\bserver_health\b", haystack):
            add(task_id, task, reason="health_check_like_task")
            continue

    visible_limited = candidates[:max_candidates]
    if len(visible_limited) == 1:
        return TaskResolution(status="single", task_id=str(visible_limited[0]["id"]), candidates=visible_limited, reason="one visible matching task")
    if len(visible_limited) > 1:
        return TaskResolution(status="multiple", candidates=visible_limited, reason="multiple visible matching tasks")

    for phrase, task_id in sorted((task_aliases or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if phrase_matches(lowered, phrase):
            add(task_id, reason=f"alias:{phrase}")

    limited = candidates[:max_candidates]
    if len(limited) == 1:
        return TaskResolution(status="single", task_id=str(limited[0]["id"]), candidates=limited, reason="one matching task")
    if len(limited) > 1:
        return TaskResolution(status="multiple", candidates=limited, reason="multiple matching tasks")
    return TaskResolution(status="none", candidates=[], reason="no matching task")


def resolve_task_reference(
    user_text: str,
    visible_tasks: list[dict[str, Any]] | None = None,
    task_aliases: dict[str, str] | None = None,
    max_candidates: int = 5,
) -> tuple[str | None, list[str]]:
    resolution = resolve_task(user_text, visible_tasks=visible_tasks, task_aliases=task_aliases, max_candidates=max_candidates)
    candidates = [str(candidate.get("id")) for candidate in resolution.candidates if candidate.get("id")]
    return (resolution.task_id if resolution.status == "single" else None, candidates)


def task_candidate(task_id: str, task: dict[str, Any] | None = None) -> dict[str, Any]:
    task = task or {}
    return {
        "id": task_id,
        **({"name": task.get("name")} if task.get("name") else {}),
        **({"type": task.get("type")} if task.get("type") else {}),
        **({"enabled": task.get("enabled")} if "enabled" in task else {}),
        **({"status": task.get("status")} if task.get("status") else {}),
    }


def unsafe_reasons_for_text(text: str) -> list[str]:
    lowered = text.lower()
    reasons: list[str] = []
    for pattern, reason in UNSAFE_PATTERNS:
        if re.search(pattern, lowered):
            if reason not in reasons:
                reasons.append(reason)
    return reasons


def matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def phrase_matches(text: str, phrase: str) -> bool:
    phrase = re.escape(phrase.strip().lower())
    if not phrase:
        return False
    return bool(re.search(rf"(?<![a-z0-9_]){phrase}(?![a-z0-9_])", text))


def normalize_match_text(value: str) -> str:
    text = value.lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9äöüß ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def significant_task_terms(normalized_text: str) -> list[str]:
    stopwords = {
        "the",
        "my",
        "me",
        "now",
        "please",
        "den",
        "die",
        "das",
        "der",
        "mir",
        "bitte",
        "jetzt",
        "task",
        "automation",
        "automations",
        "workflow",
        "workflows",
        "aufgabe",
        "automationen",
    }
    terms = [term for term in normalized_text.split() if len(term) >= 3 and term not in stopwords]
    keep = [term for term in terms if term in {"brief", "briefing", "digest", "health", "check", "backup", "security", "server", "monitor"}]
    return keep[:4]


def is_help_or_chat_question(text: str) -> bool:
    compact = normalize_match_text(text).strip()
    if not compact:
        return False
    if re.match(r"^(hello|hi|hey|dear old friend|greetings)\b", compact):
        return True
    if re.match(r"^(how|what|why|where|when|who|wie|was|warum|wo|wann|wer)\b", compact):
        if not re.search(r"\b(show|list|run|pause|send|create|draft|schedule|zeige|liste|starte|pausiere|erstelle)\b", compact):
            return True
    return bool(
        re.match(
            r"^(can|could|would|kannst|könntest|koenntest) .* (explain|tell|describe|erklären|erklaeren|beschreiben)\b",
            compact,
        )
    )


def existing_operation_kind(lowered: str) -> AutomationRequestKind | None:
    if re.search(rf"\b{RUN_VERBS}\b", lowered):
        return AutomationRequestKind.RUN_EXISTING
    if re.search(rf"\b{PAUSE_VERBS}\b", lowered):
        return AutomationRequestKind.PAUSE_EXISTING
    if re.search(rf"\b{INSPECT_VERBS}\b", lowered):
        return AutomationRequestKind.INSPECT_EXISTING
    return None


def looks_like_existing_modification(lowered: str) -> bool:
    if not re.search(r"\b(brief|briefing|digest|automation|task|workflow|aufgabe|automation)\b", lowered):
        return False
    return bool(
        re.search(rf"\b{MODIFY_VERBS}\b", lowered)
        or re.search(r"\bnimm\b.*\bauf\b", lowered)
        or re.search(r"\bbetter|besser\b", lowered)
    )


def has_specific_change(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\bbetter|besser|improve|verbesser\b", lowered):
        return False
    if re.search(r"\b(add|include|cover|remove|drop|exclude|nimm|nehme|füge|fuege|entferne)\b", lowered):
        return True
    if re.search(r"\b(cisa|nvd|kev|cve|docker|ollama|open webui|n8n|ubuntu|source|quelle|topic|subject|thema)\b", lowered):
        return True
    if re.search(r"\b(schedule|zeitplan|uhr|discord|alerts|briefings)\b", lowered):
        return True
    return False


def looks_like_create_request(lowered: str) -> bool:
    return bool(re.search(rf"\b{CREATE_VERBS}\b", lowered))


def looks_like_new_task_request(lowered: str) -> bool:
    return bool(
        re.search(
            r"\b(draft|create|set up|setup|schedule|build|prepare|monitor|watch|keep an eye|"
            r"erstelle|richte|plane|überwache|ueberwache|beobachte)\b",
            lowered,
        )
    )


def infer_registered_capability(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(printer|toner|ink|cartridge|supply|supplies)\b", lowered):
        return "printer_supply_status.v1"
    health_subject = re.search(
        r"\b(ai stack|local ai stack|health|broken|service|endpoint|server|open webui|ollama|automation api|worker|n8n|"
        r"überwache|ueberwache|beobachte)\b",
        lowered,
    )
    health_action = re.search(
        r"\b(monitor|watch|keep an eye|health|broken|check|status|anomal(?:y|ies)|überwache|ueberwache|beobachte|"
        r"prüfe|pruefe)\b",
        lowered,
    )
    if health_subject and health_action and not re.search(r"\bwebhook\b", lowered):
        return "server_health.v1"
    if re.search(r"\b(n8n|webhook)\b", lowered):
        return "n8n_webhook.v1"
    if re.search(r"\b(brief|briefing|digest|newsletter|summary|summar|news|nachrichten)\b", lowered):
        return "topic_digest.v1"
    return None


def looks_like_useful_unsupported_automation(lowered: str) -> bool:
    if re.search(r"\b(automate|automation|monitor|watch|check|remind|notify|track|alert|überwache|ueberwache|beobachte|prüfe|pruefe)\b", lowered):
        return True
    if re.search(r"\b(calendar|email|github|issue|ticket|snmp|ups|home assistant|node-red)\b", lowered):
        return True
    if re.search(
        r"\b(sonoff|minir4m|lights?|lamps?|motion|presence|absence|wifi|wi-fi|smart home|matter|zigbee|turn off|turn on|switch off|switch on)\b",
        lowered,
    ):
        return True
    return False
