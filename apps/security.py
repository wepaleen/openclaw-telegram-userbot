"""Security layer: roles, permissions, input sanitization, prompt injection defense."""

import logging
import re
from enum import Enum
from typing import Any

from config import settings

log = logging.getLogger("security")


# ── Roles ──


class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"
    BLOCKED = "blocked"


def get_user_role(user_id: int | None) -> Role:
    """Get role for a Telegram user ID."""
    if user_id is None:
        return Role.BLOCKED
    if user_id in settings.blocked_user_ids:
        return Role.BLOCKED
    if user_id in settings.admin_user_ids:
        return Role.ADMIN
    if settings.allowed_user_ids and user_id not in settings.allowed_user_ids:
        return Role.BLOCKED
    return Role.USER


# ── Tool permissions per role ──

# Tools available to regular users
_USER_TOOLS = {
    "list_available_chats",
    "list_contacts",
    "resolve_recipient",
    "resolve_target_context",
    "parse_time",
    "create_task",
    "update_task",
    "list_tasks",
    "complete_task",
    "set_reminder",
    "cancel_reminder",
    "list_reminders",
    "list_overdue_tasks",
    "search_messages",
    "get_recent_context",
    "send_message",
    "send_private_message",
    "forward_message",
}

# Additional tools for admins only
_ADMIN_ONLY_TOOLS = {
    "schedule_action",
    "list_scheduled_actions",
    "cancel_scheduled_action",
    "inspect_delayed_items",
    "list_audit_log",
    "pin_message",
    "list_chat_members",
    "list_topic_participants",
}


def is_tool_allowed(tool_name: str, role: Role) -> bool:
    """Check if a tool is allowed for the given role."""
    if role == Role.BLOCKED:
        return False
    if role == Role.ADMIN:
        return True  # admins can use everything
    return tool_name in _USER_TOOLS


def get_allowed_tools(role: Role) -> set[str]:
    """Get the set of tool names allowed for a role."""
    if role == Role.BLOCKED:
        return set()
    if role == Role.ADMIN:
        return _USER_TOOLS | _ADMIN_ONLY_TOOLS
    return _USER_TOOLS.copy()


def filter_tool_schemas(tools: list[dict[str, Any]], role: Role) -> list[dict[str, Any]]:
    """Filter tool schemas list to only include tools allowed for this role."""
    allowed = get_allowed_tools(role)
    return [t for t in tools if t.get("function", {}).get("name") in allowed]


# ── Input sanitization ──

# Patterns that look like prompt injection attempts
_INJECTION_PATTERNS = [
    # System prompt override attempts
    re.compile(r"(?:ignore|forget|disregard)\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions|rules|prompts?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a\s+)?(?:new|different)", re.IGNORECASE),
    re.compile(r"(?:new|override|replace)\s+system\s+(?:prompt|instructions?|message)", re.IGNORECASE),
    re.compile(r"```\s*system\s*\n", re.IGNORECASE),
    re.compile(r"\[SYSTEM\]|\[INST\]|\<\|system\|?\>", re.IGNORECASE),

    # Dangerous shell/code execution attempts
    re.compile(r"(?:execute|run|eval|exec)\s+(?:this\s+)?(?:command|code|script|shell|bash|python|sh\s)", re.IGNORECASE),
    re.compile(r"(?:os\.system|subprocess|__import__|eval\(|exec\(|shell_exec|system\()", re.IGNORECASE),
    re.compile(r"(?:rm\s+-rf|sudo\s|chmod\s|chown\s|wget\s|curl\s.*\|\s*(?:sh|bash)|apt\s+install|pip\s+install)", re.IGNORECASE),
    re.compile(r"(?:reverse\s+shell|bind\s+shell|nc\s+-[el]|ncat\s|/dev/tcp/)", re.IGNORECASE),

    # File system access attempts
    re.compile(r"(?:read|write|cat|open|access)\s+(?:file\s+)?(?:/etc/|/home/|/root/|/var/|~\/|\.env|\.ssh|password|credential|secret|token)", re.IGNORECASE),
    re.compile(r"(?:\.\.\/|\.\.\\)+", re.IGNORECASE),  # path traversal
]

# Dangerous content in tool arguments
_DANGEROUS_ARG_PATTERNS = [
    re.compile(r"(?:os\.system|subprocess|__import__|eval\(|exec\()", re.IGNORECASE),
    re.compile(r"(?:rm\s+-rf|sudo\s|wget\s.*\|\s*(?:sh|bash))", re.IGNORECASE),
    re.compile(r"(?:\.\.\/)+", re.IGNORECASE),
]


class SecurityViolation(Exception):
    """Raised when a security check fails."""


def check_input_safety(text: str) -> str | None:
    """Check input text for prompt injection. Returns warning message or None if safe."""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            log.warning("Prompt injection detected: %s (pattern: %s)", text[:100], pattern.pattern[:50])
            return "Запрос заблокирован системой безопасности."
    return None


def sanitize_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Sanitize tool arguments, stripping dangerous content."""
    sanitized = {}
    for key, value in args.items():
        if isinstance(value, str):
            for pattern in _DANGEROUS_ARG_PATTERNS:
                if pattern.search(value):
                    log.warning(
                        "Dangerous content in tool arg: tool=%s key=%s value=%s",
                        tool_name, key, value[:100],
                    )
                    raise SecurityViolation(
                        f"Аргумент '{key}' содержит потенциально опасный контент."
                    )
        sanitized[key] = value
    return sanitized


# ── Response filtering for non-admins ──

# Fields to strip from tool results for regular users
_SENSITIVE_FIELDS = {
    "access_hash",
    "session_key",
    "raw_context_ref",
    "idempotency_key",
}


def filter_result_for_role(result: dict[str, Any], role: Role) -> dict[str, Any]:
    """Strip sensitive fields from tool results for non-admin users."""
    if role == Role.ADMIN:
        return result

    filtered = {}
    for key, value in result.items():
        if key in _SENSITIVE_FIELDS:
            continue
        if isinstance(value, dict):
            filtered[key] = filter_result_for_role(value, role)
        elif isinstance(value, list):
            filtered[key] = [
                filter_result_for_role(item, role) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            filtered[key] = value
    return filtered
