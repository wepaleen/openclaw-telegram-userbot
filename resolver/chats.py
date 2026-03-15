"""Chat/topic index with DB caching."""

import json
import logging
from typing import Any

from db import get_db

log = logging.getLogger("chats")


def _chat_aliases(row: dict[str, Any]) -> list[str]:
    try:
        aliases = json.loads(row.get("aliases", "[]"))
    except json.JSONDecodeError:
        return []
    return [str(alias) for alias in aliases if str(alias).strip()]


async def sync_chats(tg_api: Any) -> None:
    """Sync available chats from Telegram into the DB index."""
    db = await get_db()
    chats = await tg_api.list_available_chats(limit=100)
    for c in chats:
        if "error" in c:
            continue
        await db.execute(
            "INSERT OR REPLACE INTO chat_index "
            "(chat_id, title, username, chat_type, is_forum, last_synced) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (c["chat_id"], c["title"], c.get("username"), c.get("type", "group"),
             1 if c.get("is_forum") else 0),
        )
    await db.commit()
    log.info("Synced %d chats to index", len(chats))


async def sync_topics(tg_api: Any, chat_id: int) -> None:
    """Sync forum topics for a chat."""
    db = await get_db()
    result = await tg_api.list_forum_topics(chat_id, limit=50)
    for t in result.get("topics", []):
        await db.execute(
            "INSERT OR REPLACE INTO topic_index "
            "(chat_id, topic_id, title, top_message_id, last_synced) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (chat_id, t["topic_id"], t.get("title", ""), t.get("top_message_id")),
        )
    await db.commit()
    log.info("Synced topics for chat %d", chat_id)


async def find_chat(query: str) -> dict[str, Any] | None:
    """Find a chat by title, username, alias, or ID."""
    matches = await search_chats(query)
    if len(matches) == 1:
        return matches[0]
    return None


async def search_chats(query: str) -> list[dict[str, Any]]:
    """Return exact/partial chat matches from the local index."""
    db = await get_db()
    q = query.strip().lower()
    if not q:
        return []

    matches: list[dict[str, Any]] = []
    seen_chat_ids: set[int] = set()

    def add_match(row: dict[str, Any]) -> None:
        chat_id = int(row["chat_id"])
        if chat_id in seen_chat_ids:
            return
        seen_chat_ids.add(chat_id)
        matches.append(row)

    # By chat_id
    if q.lstrip("-").isdigit():
        rows = await db.execute_fetchall(
            "SELECT * FROM chat_index WHERE chat_id = ?", (int(q),)
        )
        for row in rows:
            add_match(dict(row))
        if matches:
            return matches

    # By username
    if q.startswith("@"):
        rows = await db.execute_fetchall(
            "SELECT * FROM chat_index WHERE LOWER(username) = ?", (q,)
        )
        for row in rows:
            add_match(dict(row))
        if matches:
            return matches

    # By title (exact)
    rows = await db.execute_fetchall(
        "SELECT * FROM chat_index WHERE LOWER(title) = ?", (q,)
    )
    for row in rows:
        add_match(dict(row))
    if matches:
        return matches

    # By alias (exact)
    all_chats = await db.execute_fetchall("SELECT * FROM chat_index")
    for c in all_chats:
        c_dict = dict(c)
        for alias in _chat_aliases(c_dict):
            if alias.lower() == q:
                add_match(c_dict)
                break
    if matches:
        return matches

    # By title (partial)
    rows = await db.execute_fetchall(
        "SELECT * FROM chat_index WHERE LOWER(title) LIKE ?", (f"%{q}%",)
    )
    for row in rows:
        add_match(dict(row))

    for c in all_chats:
        c_dict = dict(c)
        for alias in _chat_aliases(c_dict):
            if q in alias.lower():
                add_match(c_dict)
                break

    return matches


async def find_topic(chat_id: int, query: str) -> dict[str, Any] | None:
    """Find a topic by title or ID within a chat."""
    matches = await search_topics(chat_id, query)
    if len(matches) == 1:
        return matches[0]
    return None


async def search_topics(chat_id: int, query: str) -> list[dict[str, Any]]:
    """Return exact/partial topic matches for a forum chat."""
    db = await get_db()
    q = query.strip().lower()
    if not q:
        return []

    matches: list[dict[str, Any]] = []
    seen_topic_ids: set[int] = set()

    def add_match(row: dict[str, Any]) -> None:
        topic_id = int(row["topic_id"])
        if topic_id in seen_topic_ids:
            return
        seen_topic_ids.add(topic_id)
        matches.append(row)

    if q.isdigit():
        rows = await db.execute_fetchall(
            "SELECT * FROM topic_index WHERE chat_id = ? AND topic_id = ?",
            (chat_id, int(q)),
        )
        for row in rows:
            add_match(dict(row))
        if matches:
            return matches

    # Exact title
    rows = await db.execute_fetchall(
        "SELECT * FROM topic_index WHERE chat_id = ? AND LOWER(title) = ?",
        (chat_id, q),
    )
    for row in rows:
        add_match(dict(row))
    if matches:
        return matches

    # Partial title
    rows = await db.execute_fetchall(
        "SELECT * FROM topic_index WHERE chat_id = ? AND LOWER(title) LIKE ?",
        (chat_id, f"%{q}%"),
    )
    for row in rows:
        add_match(dict(row))

    return matches


async def get_chats_summary() -> str:
    """Return chats as JSON string for LLM prompts."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT chat_id, title, username, chat_type, is_forum FROM chat_index "
        "ORDER BY title LIMIT 50"
    )
    result = []
    for r in rows:
        entry = {"chat_id": r["chat_id"], "title": r["title"]}
        if r["username"]:
            entry["username"] = r["username"]
        if r["is_forum"]:
            entry["is_forum"] = True
        result.append(entry)
    return json.dumps(result, ensure_ascii=False)


async def get_all_chat_ids() -> list[int]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT chat_id FROM chat_index")
    return [r["chat_id"] for r in rows]


async def get_forum_chat_ids() -> list[int]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT chat_id FROM chat_index WHERE is_forum = 1"
    )
    return [r["chat_id"] for r in rows]
