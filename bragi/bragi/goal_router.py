from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AutomationRequestKind(str, Enum):
    LIST_EXISTING = "list_existing"
    INSPECT_EXISTING = "inspect_existing"
    RUN_EXISTING = "run_existing"
    PAUSE_EXISTING = "pause_existing"
    MODIFY_EXISTING = "modify_existing"
    CREATE_NEW = "create_new"
    PROPOSE_NEW_CAPABILITY = "propose_new_capability"
    UNSAFE = "unsafe"
    NEEDS_CLARIFICATION = "needs_clarification"
    CHAT = "chat"


class AutomationTargetKind(str, Enum):
    EXISTING_TASK = "existing_task"
    NEW_TASK = "new_task"
    NEW_CAPABILITY = "new_capability"
    UNKNOWN = "unknown"


class AutomationRequestClassification(BaseModel):
    request_kind: AutomationRequestKind
    target_kind: AutomationTargetKind = AutomationTargetKind.UNKNOWN
    target_task_id: str | None = None
    target_task_candidates: list[str] = Field(default_factory=list)
    capability_id: str | None = None
    operation: dict[str, Any] | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_information: list[str] = Field(default_factory=list)
    unsafe_reasons: list[str] = Field(default_factory=list)
    reason: str = ""


TASK_ID_RE = re.compile(r"\b([a-z][a-z0-9_]{2,127})\b")

UNSAFE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bapproval nonce\b|\bnonce\b", "approval nonces are not available to Bragi"),
    (r"\badmin(?:\s+api)?\s+key\b|\bapi key\b|\btoken\b|\bpassword\b|\bprivate key\b", "secrets and admin credentials are forbidden"),
    (r"\bapprove\b|\breject\b|\bgenehmig|\bablehnen\b", "approval decisions must stay in the local admin path"),
    (r"\bdocker socket\b|\bdocker exec\b|\b/var/run/docker\.sock\b", "Docker socket and Docker exec access are forbidden"),
    (r"\brestart docker\b|\bstart docker\b|\brestart.*services?\b|\bstarte .*dienste neu\b|\bdienste neu\b", "restart docker or service restarts are host administration"),
    (r"\bshell\b|\bcommand\b|\bterminal\b|\bbash\b|\bpowershell\b", "shell execution is forbidden"),
    (r"\bfirewall\b|\biptables\b|\bufw\b|\brouter\b", "firewall or router changes are security-sensitive"),
    (r"\bdelete files?\b|\breorganize files?\b|\blösche\b|\bloesche\b|\bdateien.*(löschen|loeschen|umorganisieren)\b", "broad filesystem mutation is forbidden"),
    (r"\binstall(?:iere)? .*updates?\b|\bautomatic(?:ally)? updates?\b|\bauto(?:matically)? update\b", "automatic updates are not a model-facing action"),
    (r"\bwebhook url\b|https?://[^ ]*webhook", "raw webhook URLs are forbidden"),
    (r"\bpurchase\b|\bbuy\b|\bkaufen\b", "financial transactions are forbidden"),
)

LIST_PATTERNS = (
    r"\blist\b.*\b(tasks?|automations?)\b",
    r"\bshow all\b.*\b(tasks?|automations?)\b",
    r"\bwhat\b.*\b(tasks?|automations?)\b",
    r"\bliste\b.*\b(aufgaben|automationen)\b",
    r"\bzeige\b.*\b(alle|meine)\b.*\b(aufgaben|automationen)\b",
    r"\bwelche\b.*\b(aufgaben|automationen)\b",
)
INSPECT_VERBS = r"(show|get|inspect|status|details?|view|zeig|zeige|status|details?|anzeigen)"
RUN_VERBS = r"(run|execute|dry run|send|deliver|generate|start|schick|schicke|sende|ausfuehr|ausführ|starte)"
PAUSE_VERBS = r"(pause|disable|stop|pausiere|deaktiviere|stoppe|halte\s+an)"
MODIFY_VERBS = r"(add|include|cover|remove|drop|exclude|change|modify|update|improve|make|nimm|nehme|füge|fuege|entferne|ändere|aendere|mach)"
CREATE_VERBS = r"(draft|create|set up|setup|schedule|build|prepare|monitor|watch|keep an eye|check|erstelle|richte|plane|überwache|ueberwache|beobachte|prüfe|pruefe)"


def classify_automation_request(
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
            request_kind=AutomationRequestKind.CHAT,
            confidence=0.90,
            reason="help, explanation, or ordinary chat request",
        )

    target_task_id, candidates = resolve_task_reference(
        text,
        visible_tasks=visible_tasks,
        task_aliases=task_aliases,
        max_candidates=max_candidates,
    )
    candidates = candidates[:max(1, max_candidates)]

    existing_kind = existing_operation_kind(lowered)
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
            )
        return AutomationRequestClassification(
            request_kind=AutomationRequestKind.NEEDS_CLARIFICATION,
            target_kind=AutomationTargetKind.EXISTING_TASK,
            target_task_candidates=candidates,
            confidence=0.70 if candidates else 0.56,
            missing_information=["target_task_id"],
            reason="existing-task operation is missing an unambiguous task target",
        )

    if looks_like_existing_modification(lowered):
        missing: list[str] = []
        if not target_task_id:
            missing.append("target_task_id")
        if not has_specific_change(text):
            missing.append("change_type")
        if candidates and not target_task_id:
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


def resolve_task_reference(
    user_text: str,
    visible_tasks: list[dict[str, Any]] | None = None,
    task_aliases: dict[str, str] | None = None,
    max_candidates: int = 5,
) -> tuple[str | None, list[str]]:
    text = str(user_text or "")
    lowered = text.lower()
    candidates: list[str] = []

    def add(task_id: str | None) -> None:
        if task_id and task_id not in candidates:
            candidates.append(task_id)

    visible_by_id = {
        str(task.get("id")): task
        for task in (visible_tasks or [])
        if isinstance(task, dict) and task.get("id")
    }

    for match in TASK_ID_RE.finditer(lowered):
        token = match.group(1)
        if "_" in token:
            if not visible_by_id or token in visible_by_id:
                add(token)

    for phrase, task_id in sorted((task_aliases or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if phrase_matches(lowered, phrase):
            add(task_id)

    normalized_text = normalize_match_text(text)
    significant_text_terms = significant_task_terms(normalized_text)
    for task in visible_by_id.values():
        task_id = str(task.get("id") or "")
        normalized_id = normalize_match_text(task_id.replace("_", " "))
        normalized_name = normalize_match_text(str(task.get("name") or ""))
        normalized_type = normalize_match_text(str(task.get("type") or ""))
        haystack = " ".join(part for part in (normalized_id, normalized_name, normalized_type) if part)
        if normalized_id and normalized_id in normalized_text:
            add(task_id)
            continue
        if normalized_name and normalized_name in normalized_text:
            add(task_id)
            continue
        if normalized_text and normalized_name and normalized_text in normalized_name and len(normalized_text) >= 8:
            add(task_id)
            continue
        if significant_text_terms and all(term in haystack for term in significant_text_terms):
            add(task_id)
            continue
        if "brief" in significant_text_terms and re.search(r"\b(brief|briefing|digest)\b", haystack):
            add(task_id)
            continue
        if {"health", "check"} <= set(significant_text_terms) and re.search(r"\bhealth\b.*\bcheck\b|\bserver_health\b", haystack):
            add(task_id)
            continue

    limited = candidates[: max(1, max_candidates)]
    if len(limited) == 1:
        return limited[0], limited
    return None, limited


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
        "aufgabe",
        "automationen",
    }
    terms = [term for term in normalized_text.split() if len(term) >= 3 and term not in stopwords]
    keep = [term for term in terms if term in {"brief", "briefing", "digest", "health", "check", "backup", "security", "server"}]
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
    if not re.search(r"\b(brief|briefing|digest|automation|task|aufgabe|automation)\b", lowered):
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


def infer_registered_capability(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(printer|toner|ink|cartridge|supply|supplies)\b", lowered):
        return "printer_supply_status.v1"
    if re.search(r"\b(n8n|webhook)\b", lowered):
        return "n8n_webhook.v1"
    if re.search(r"\b(health|broken|service|endpoint|server|open webui|ollama|automation api|worker|überwache|ueberwache|beobachte)\b", lowered):
        if re.search(r"\b(monitor|watch|keep an eye|health|broken|check|überwache|ueberwache|beobachte|prüfe|pruefe)\b", lowered):
            return "server_health.v1"
    if re.search(r"\b(brief|briefing|digest|newsletter|summary|summar|news|nachrichten)\b", lowered):
        return "topic_digest.v1"
    return None


def looks_like_useful_unsupported_automation(lowered: str) -> bool:
    if re.search(r"\b(automate|automation|monitor|watch|check|remind|notify|track|alert|überwache|ueberwache|beobachte|prüfe|pruefe)\b", lowered):
        return True
    if re.search(r"\b(calendar|email|github|issue|ticket|printer|snmp|ups|toner|home assistant|node-red)\b", lowered):
        return True
    return False
