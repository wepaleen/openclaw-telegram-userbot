"""Chat/topic index synchronization for the Telethon-backed runtime."""

from __future__ import annotations

import logging
from typing import Any

from apps.task_core.db import get_db
from apps.telethon_bridge.service import TelethonBridgeService

log = logging.getLogger("telethon_bridge.index_sync")


async def sync_chat_index(
    transport: TelethonBridgeService,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Sync visible dialogs from Telethon into the shared chat index."""
    db = await get_db()
    rows = await transport.list_dialog_rows(limit=limit)
    for row in rows:
        await db.execute(
            "INSERT OR REPLACE INTO chat_index "
            "(chat_id, title, username, chat_type, is_forum, last_synced) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (
                row["chat_id"],
                row["title"],
                row.get("username"),
                row.get("type", row.get("peer_type", "channel")),
                1 if row.get("is_forum") else 0,
            ),
        )
    await db.commit()
    log.info("Synced %d dialogs into chat_index", len(rows))
    return rows


async def sync_topic_index(
    transport: TelethonBridgeService,
    *,
    chat_id: int,
    limit: int = 100,
) -> int:
    """Sync forum topics for one forum chat into the shared topic index."""
    db = await get_db()
    result = await transport.list_forum_topics(chat_id, limit=limit)
    topics = result.get("topics", [])
    for topic in topics:
        await db.execute(
            "INSERT OR REPLACE INTO topic_index "
            "(chat_id, topic_id, title, top_message_id, last_synced) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (
                chat_id,
                topic["topic_id"],
                topic.get("title") or "",
                topic.get("top_message_id"),
            ),
        )
    await db.commit()
    log.info("Synced %d topics for chat %d", len(topics), chat_id)
    return len(topics)


async def sync_all_indexes(
    transport: TelethonBridgeService,
    *,
    chats_limit: int = 200,
    topics_limit: int = 100,
) -> None:
    """Sync dialogs and all discovered forum topics for the new Telethon runtime."""
    rows = await sync_chat_index(transport, limit=chats_limit)
    forum_chat_ids = [int(row["chat_id"]) for row in rows if row.get("is_forum")]
    for chat_id in forum_chat_ids:
        try:
            await sync_topic_index(transport, chat_id=chat_id, limit=topics_limit)
        except Exception as e:
            log.warning("Topic sync failed for %s: %s", chat_id, e)
