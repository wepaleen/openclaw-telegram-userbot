"""Audit log helpers for the task core service."""

import json
import logging
import time

from apps.task_core.db import get_db

log = logging.getLogger("task_core.audit")


async def log_action(
    action_type: str,
    intent: str | None = None,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
    source_user_id: int | None = None,
    target_chat_id: int | None = None,
    target_topic_id: int | None = None,
    params: dict | None = None,
    result: dict | None = None,
    success: bool = True,
    error: str | None = None,
    llm_used: bool = False,
    latency_ms: int | None = None,
) -> None:
    """Persist an action execution record into the audit log."""
    try:
        db = await get_db()
        await db.execute(
            "INSERT INTO audit_log "
            "(action_type, intent, source_chat_id, source_message_id, "
            "source_user_id, target_chat_id, target_topic_id, "
            "params, result, success, error, llm_used, latency_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_type,
                intent,
                source_chat_id,
                source_message_id,
                source_user_id,
                target_chat_id,
                target_topic_id,
                json.dumps(params or {}, ensure_ascii=False),
                json.dumps(result or {}, ensure_ascii=False)[:2000],
                1 if success else 0,
                error,
                1 if llm_used else 0,
                latency_ms,
            ),
        )
        await db.commit()
    except Exception as e:
        log.error("Failed to write audit log: %s", e)


class Timer:
    """Simple context manager to measure elapsed time in ms."""

    def __init__(self) -> None:
        self.start = 0.0
        self.elapsed_ms = 0

    def __enter__(self) -> "Timer":
        self.start = time.monotonic()
        return self

    def __exit__(self, *args: object) -> None:
        self.elapsed_ms = int((time.monotonic() - self.start) * 1000)
