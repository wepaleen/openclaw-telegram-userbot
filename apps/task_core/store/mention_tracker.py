"""Track @mention counts to avoid spamming tags in group chats.

Logic:
- Each time the bot tags @username in a chat/topic, increment the counter.
- If counter >= limit (default 2), the bot should switch to DM.
- When the mentioned person sends ANY message in the same chat/topic,
  reset their counter (they responded, no need to keep tagging).
"""

import logging
from datetime import datetime, timezone
from typing import Any

from apps.task_core.db import get_db

log = logging.getLogger("task_core.store.mention_tracker")

DEFAULT_MENTION_LIMIT = 2


async def record_mention(
    mentioned_username: str,
    chat_id: int,
    topic_id: int | None = None,
) -> dict[str, Any]:
    """Record that the bot tagged @username in a chat/topic. Returns current count."""
    db = await get_db()
    username = mentioned_username.lstrip("@").lower()
    now = datetime.now(timezone.utc).isoformat()

    # Upsert: increment count if exists, insert if not
    await db.execute(
        """
        INSERT INTO mention_tracker (mentioned_username, chat_id, topic_id, mention_count, last_mention_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(mentioned_username, chat_id, topic_id) DO UPDATE SET
            mention_count = CASE
                WHEN resolved_at IS NOT NULL THEN 1
                ELSE mention_count + 1
            END,
            last_mention_at = ?,
            resolved_at = NULL,
            updated_at = ?
        """,
        (username, chat_id, topic_id, now, now, now, now),
    )
    await db.commit()

    row = await db.execute(
        "SELECT mention_count FROM mention_tracker WHERE mentioned_username = ? AND chat_id = ? AND topic_id IS ?",
        (username, chat_id, topic_id),
    )
    result = await row.fetchone()
    count = result[0] if result else 1

    log.info(
        "Recorded mention: @%s in chat %s topic %s -> count=%d",
        username, chat_id, topic_id, count,
    )
    return {"username": username, "chat_id": chat_id, "topic_id": topic_id, "mention_count": count}


async def check_mention_limit(
    mentioned_username: str,
    chat_id: int,
    topic_id: int | None = None,
    limit: int = DEFAULT_MENTION_LIMIT,
) -> dict[str, Any]:
    """Check if the bot has reached the mention limit for @username in this chat/topic."""
    db = await get_db()
    username = mentioned_username.lstrip("@").lower()

    row = await db.execute(
        "SELECT mention_count, last_mention_at, resolved_at FROM mention_tracker "
        "WHERE mentioned_username = ? AND chat_id = ? AND topic_id IS ?",
        (username, chat_id, topic_id),
    )
    result = await row.fetchone()

    if not result or result["resolved_at"] is not None:
        return {
            "username": username,
            "mention_count": 0,
            "limit": limit,
            "should_dm": False,
            "resolved": result["resolved_at"] is not None if result else False,
        }

    count = result["mention_count"]
    return {
        "username": username,
        "mention_count": count,
        "limit": limit,
        "should_dm": count >= limit,
        "last_mention_at": result["last_mention_at"],
    }


async def resolve_mention(
    mentioned_username: str,
    chat_id: int,
    topic_id: int | None = None,
) -> bool:
    """Mark mentions as resolved — the person responded in this chat/topic.

    Returns True if there was an active (unresolved) mention tracker to clear.
    """
    db = await get_db()
    username = mentioned_username.lstrip("@").lower()
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        """
        UPDATE mention_tracker
        SET resolved_at = ?, mention_count = 0, updated_at = ?
        WHERE mentioned_username = ? AND chat_id = ? AND topic_id IS ?
          AND resolved_at IS NULL AND mention_count > 0
        """,
        (now, now, username, chat_id, topic_id),
    )
    await db.commit()

    if cursor.rowcount > 0:
        log.info("Resolved mentions for @%s in chat %s topic %s", username, chat_id, topic_id)
        return True
    return False


async def resolve_mentions_by_sender(
    sender_username: str,
    chat_id: int,
    topic_id: int | None = None,
) -> int:
    """When a person sends a message in a chat/topic, resolve all pending mentions for them.

    This is the main auto-resolve hook: called on every incoming message.
    Returns the number of mention trackers resolved.
    """
    db = await get_db()
    username = sender_username.lstrip("@").lower()
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        """
        UPDATE mention_tracker
        SET resolved_at = ?, mention_count = 0, updated_at = ?
        WHERE mentioned_username = ? AND chat_id = ? AND (topic_id IS ? OR topic_id IS NULL)
          AND resolved_at IS NULL AND mention_count > 0
        """,
        (now, now, username, chat_id, topic_id),
    )
    await db.commit()

    if cursor.rowcount > 0:
        log.info(
            "Auto-resolved %d mention tracker(s) for @%s in chat %s (they responded)",
            cursor.rowcount, username, chat_id,
        )
    return cursor.rowcount
