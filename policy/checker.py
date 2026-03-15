"""Confirmation policy for actions."""

import logging

from executor.actions import Action, RiskLevel
from config import settings

log = logging.getLogger("policy")

# Track which sessions have confirmed write access
_write_confirmed: set[str] = set()


def check_allowed_chat(chat_id: int) -> bool:
    return settings.is_allowed_chat(chat_id)


def needs_confirmation(action: Action, session_key: str) -> bool:
    """Check if an action needs user confirmation before execution."""
    risk = action.risk

    if risk == RiskLevel.READ:
        return False

    if risk == RiskLevel.RISKY:
        return True

    if risk == RiskLevel.WRITE:
        # First write in session requires confirmation
        if session_key not in _write_confirmed:
            return True
        return False

    return False


def confirm_session_writes(session_key: str) -> None:
    """Mark a session as having confirmed write access."""
    _write_confirmed.add(session_key)
    log.info("Write access confirmed for session: %s", session_key)


def reset_session_writes(session_key: str) -> None:
    """Drop cached write confirmation for a session."""
    _write_confirmed.discard(session_key)
    log.info("Write access reset for session: %s", session_key)


def format_confirmation(action: Action) -> str:
    """Format a confirmation message for the user."""
    t = action.type.value
    p = action.params

    if t == "send_private":
        return f"Отправить в личку {p.get('target')}: «{p.get('text', '')[:100]}»?"
    if t == "send_chat":
        return f"Отправить в чат {p.get('chat_id')}: «{p.get('text', '')[:100]}»?"
    if t == "send_topic":
        return f"Отправить в тему {p.get('topic_id')} чата {p.get('chat_id')}: «{p.get('text', '')[:100]}»?"
    if t == "send_link":
        return f"Отправить по ссылке {p.get('link')}: «{p.get('text', '')[:100]}»?"
    if t == "forward_message":
        return f"Переслать сообщение {p.get('message_id')} из {p.get('from_chat_id')} в {p.get('to_chat_id')}?"
    if t == "pin_message":
        return f"Закрепить сообщение {p.get('message_id')} в чате {p.get('chat_id')}?"

    return f"Выполнить действие {t}? (да/нет)"
