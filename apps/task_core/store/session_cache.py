"""Persistent LLM session cache for conversation continuity across restarts."""

import json
import logging
from typing import Any

from apps.task_core.db import get_db

log = logging.getLogger("task_core.session_cache")

# Max messages to keep per session (trim oldest assistant/tool rounds)
MAX_CACHED_MESSAGES = 20


async def load_session(session_key: str) -> list[dict[str, Any]]:
    """Load cached LLM messages for a session, or empty list if none."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT messages FROM session_cache WHERE session_key = ?",
        (session_key,),
    )
    row = await cursor.fetchone()
    if not row:
        return []
    try:
        messages = json.loads(row[0])
        if not isinstance(messages, list):
            return []
        return messages
    except (json.JSONDecodeError, TypeError):
        return []


async def save_session(session_key: str, messages: list[dict[str, Any]]) -> None:
    """Save LLM messages for a session, trimming to MAX_CACHED_MESSAGES."""
    # Keep system prompt + last N messages
    if len(messages) > MAX_CACHED_MESSAGES + 1:
        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        messages = system + rest[-MAX_CACHED_MESSAGES:]

    # Strip large tool results to save space
    trimmed = []
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if len(content) > 500:
                trimmed.append({**msg, "content": content[:500] + "…"})
                continue
        trimmed.append(msg)

    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO session_cache (session_key, messages, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (session_key, json.dumps(trimmed, ensure_ascii=False)),
    )
    await db.commit()


async def clear_old_sessions(max_age_hours: int = 24) -> int:
    """Remove sessions older than max_age_hours. Returns count deleted."""
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM session_cache WHERE updated_at < datetime('now', ?)",
        (f"-{max_age_hours} hours",),
    )
    await db.commit()
    return cursor.rowcount
