"""Level-1 local command parser — handles simple commands without LLM (0 tokens).

Covers:
- "напиши/отправь в [чат] [топик] текст" → send_message
- "напиши [человеку] текст" → send_private_message
- "напомни через X минут текст" → set_reminder
- "покажи задачи" → list_tasks
- "покажи напоминания" → list_reminders
- "мои задачи" → list_tasks
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("openclaw_adapter.local_commands")


@dataclass(slots=True)
class ParsedCommand:
    """A locally parsed command ready for tool execution."""

    tool_name: str
    tool_args: dict[str, Any]
    reply_template: str | None = None  # human-readable confirmation template


def try_parse_local(text: str) -> ParsedCommand | None:
    """Try to parse text as a local command. Returns None if not recognized."""
    stripped = text.strip()
    if not stripped:
        return None

    return (
        _try_send_to_topic(stripped)
        or _try_send_private(stripped)
        or _try_reminder(stripped)
        or _try_list_tasks(stripped)
        or _try_list_reminders(stripped)
        or _try_cancel_reminder(stripped)
    )


# --- Send to chat/topic ---
# "напиши в Работа текст сообщения"
# "отправь в чат Проект в топик Обсуждение текст"
# "скинь в Разработка привет всем"
_SEND_TOPIC_RE = re.compile(
    r"^(?:напиши|отправь|пошли|скинь)\s+"
    r"в\s+(?:чат\s+)?(.+?)\s+"
    r"(?:в\s+(?:топик|тему|тред)\s+(.+?)\s+)?"
    r"(?:текст\s+|сообщение\s+|:?\s*)"
    r"(.+)$",
    re.IGNORECASE | re.DOTALL,
)

# Simpler: "напиши в Работа: текст" or "напиши в Работа текст"
_SEND_CHAT_SIMPLE_RE = re.compile(
    r"^(?:напиши|отправь|пошли|скинь)\s+"
    r"в\s+(?:чат\s+)?[«\"']?(.+?)[»\"']?\s*"
    r"(?:в\s+(?:топик|тему|тред)\s+[«\"']?(.+?)[»\"']?\s*)?"
    r"[:]\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _try_send_to_topic(text: str) -> ParsedCommand | None:
    # Try colon-separated first (more reliable)
    m = _SEND_CHAT_SIMPLE_RE.match(text)
    if m:
        chat_query = m.group(1).strip()
        topic_query = m.group(2).strip() if m.group(2) else None
        message_text = m.group(3).strip()
        if chat_query and message_text:
            args: dict[str, Any] = {
                "text": message_text,
                "chat_query": chat_query,
            }
            if topic_query:
                args["topic_query"] = topic_query
            log.info("Local parse: send_message chat=%s topic=%s", chat_query, topic_query)
            return ParsedCommand(tool_name="send_message", tool_args=args)

    m = _SEND_TOPIC_RE.match(text)
    if m:
        chat_query = m.group(1).strip()
        topic_query = m.group(2).strip() if m.group(2) else None
        message_text = m.group(3).strip()
        if chat_query and message_text:
            args = {
                "text": message_text,
                "chat_query": chat_query,
            }
            if topic_query:
                args["topic_query"] = topic_query
            log.info("Local parse: send_message chat=%s topic=%s", chat_query, topic_query)
            return ParsedCommand(tool_name="send_message", tool_args=args)

    return None


# --- Send private message ---
# "напиши Герычу привет"
# "отправь @username текст"
_SEND_PRIVATE_RE = re.compile(
    r"^(?:напиши|отправь|пошли|скинь)\s+"
    r"(@\w+|[А-ЯЁа-яёA-Za-z]+(?:\s+[А-ЯЁа-яёA-Za-z]+)?)\s*"
    r"[:：]?\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)

# Exclude chat-targeting keywords
_CHAT_KEYWORDS = {"в", "на", "из", "чат", "группу", "канал", "топик", "тему", "тред"}


def _try_send_private(text: str) -> ParsedCommand | None:
    m = _SEND_PRIVATE_RE.match(text)
    if not m:
        return None

    target = m.group(1).strip()
    message_text = m.group(2).strip()

    # Don't match "напиши в ..." — that's chat targeting
    if target.lower() in _CHAT_KEYWORDS:
        return None

    if not message_text:
        return None

    log.info("Local parse: send_private_message target=%s", target)
    return ParsedCommand(
        tool_name="send_private_message",
        tool_args={"target_query": target, "text": message_text},
    )


# --- Reminders ---
# "напомни через 5 минут позвонить"
# "напомни через час проверить"
# "напомни в 18:30 тест"
# "напомни завтра в 10:00 сделать отчёт"
_REMIND_DELTA_RE = re.compile(
    r"^напомни\s+(через\s+.+?)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)

_REMIND_TIME_RE = re.compile(
    r"^напомни\s+(в\s+\d{1,2}[:.]\d{2}|завтра(?:\s+в\s+\d{1,2}[:.]\d{2})?|сегодня\s+в\s+\d{1,2}[:.]\d{2})\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _try_reminder(text: str) -> ParsedCommand | None:
    for pattern in (_REMIND_DELTA_RE, _REMIND_TIME_RE):
        m = pattern.match(text)
        if m:
            time_phrase = m.group(1).strip()
            reminder_text = m.group(2).strip()
            if time_phrase and reminder_text:
                log.info("Local parse: set_reminder time=%s text=%s", time_phrase, reminder_text[:50])
                return ParsedCommand(
                    tool_name="set_reminder",
                    tool_args={"time_phrase": time_phrase, "text": reminder_text},
                )
    return None


# --- List tasks ---
# "покажи задачи", "мои задачи", "список задач", "задачи"
_LIST_TASKS_RE = re.compile(
    r"^(?:покажи|список|мои|все|открытые)?\s*задач[аеиуёы]\s*$",
    re.IGNORECASE,
)


def _try_list_tasks(text: str) -> ParsedCommand | None:
    if _LIST_TASKS_RE.match(text):
        log.info("Local parse: list_tasks")
        return ParsedCommand(tool_name="list_tasks", tool_args={})
    return None


# --- List reminders ---
# "покажи напоминания", "мои напоминания", "напоминания"
_LIST_REMINDERS_RE = re.compile(
    r"^(?:покажи|список|мои|все|активные)?\s*напоминани[яейю]\s*$",
    re.IGNORECASE,
)


def _try_list_reminders(text: str) -> ParsedCommand | None:
    if _LIST_REMINDERS_RE.match(text):
        log.info("Local parse: list_reminders")
        return ParsedCommand(tool_name="list_reminders", tool_args={})
    return None


# --- Cancel reminder ---
# "отмени напоминание 3", "удали напоминание 5"
_CANCEL_REMINDER_RE = re.compile(
    r"^(?:отмени|удали|убери)\s+напоминание\s+(\d+)$",
    re.IGNORECASE,
)


def _try_cancel_reminder(text: str) -> ParsedCommand | None:
    m = _CANCEL_REMINDER_RE.match(text)
    if m:
        reminder_id = int(m.group(1))
        log.info("Local parse: cancel_reminder id=%d", reminder_id)
        return ParsedCommand(
            tool_name="cancel_reminder",
            tool_args={"reminder_id": reminder_id},
        )
    return None
