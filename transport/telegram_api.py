"""Low-level Pyrogram wrappers for all Telegram operations."""

from typing import Any

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.raw.functions.channels import GetForumTopics, GetForumTopicsByID

from config import normalize_chat_id, settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def chat_label(chat: Any) -> str:
    title = getattr(chat, "title", None)
    first_name = getattr(chat, "first_name", None)
    last_name = getattr(chat, "last_name", None)
    username = getattr(chat, "username", None)
    if title:
        return title
    if first_name or last_name:
        return " ".join(x for x in [first_name, last_name] if x).strip()
    if username:
        return f"@{username}"
    return str(getattr(chat, "id", "unknown"))


def serialize_chat(chat: Any) -> dict[str, Any]:
    username = getattr(chat, "username", None)
    return {
        "chat_id": getattr(chat, "id", None),
        "title": chat_label(chat),
        "username": f"@{username}" if username else None,
        "type": str(getattr(chat, "type", "")),
        "is_forum": bool(getattr(chat, "is_forum", False)),
    }


def serialize_message(m: Message) -> dict[str, Any]:
    text = (m.text or m.caption or "").strip()
    sender = None
    if m.from_user:
        sender = {
            "id": m.from_user.id,
            "name": " ".join(
                x for x in [m.from_user.first_name, m.from_user.last_name] if x
            ).strip() or m.from_user.username or str(m.from_user.id),
            "username": m.from_user.username,
        }
    elif m.sender_chat:
        sender = {
            "id": m.sender_chat.id,
            "name": m.sender_chat.title or str(m.sender_chat.id),
            "username": m.sender_chat.username,
        }
    return {
        "id": m.id,
        "chat_id": m.chat.id,
        "chat_title": m.chat.title or m.chat.first_name,
        "date": m.date.isoformat() if m.date else None,
        "sender": sender,
        "text": text,
        "reply_to_message_id": getattr(m, "reply_to_message_id", None),
        "reply_to_top_message_id": getattr(m, "reply_to_top_message_id", None),
    }


def normalize_target(target: str) -> str | int:
    value = str(target).strip()
    if not value:
        raise ValueError("target is empty")
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    else:
        if value.startswith("tg://resolve?domain="):
            value = value.split("domain=", 1)[1].split("&", 1)[0]
    value = value.strip().strip("/").split("?", 1)[0].split("/", 1)[0]
    if not value:
        raise ValueError("target is empty")
    if value.lstrip("-").isdigit():
        return int(value)
    return value if value.startswith("@") else f"@{value}"


def topic_message_matches(message: Message, top_message_id: int) -> bool:
    return (
        message.id == top_message_id
        or getattr(message, "reply_to_top_message_id", None) == top_message_id
    )


# ---------------------------------------------------------------------------
# Telegram API operations
# ---------------------------------------------------------------------------

class TelegramAPI:
    """Wraps all Pyrogram operations for the bot."""

    def __init__(self, client: Client):
        self.app = client

    # --- Forum topics ---

    async def require_forum_chat(self, chat_id: int) -> Any:
        chat = await self.app.get_chat(chat_id)
        if not getattr(chat, "is_forum", False):
            raise ValueError(f'chat "{chat_label(chat)}" ({chat.id}) is not a forum')
        return chat

    async def get_topic_meta(self, chat_id: int, topic_id: int) -> dict[str, Any] | None:
        await self.require_forum_chat(chat_id)
        peer = await self.app.resolve_peer(chat_id)
        res = await self.app.invoke(GetForumTopicsByID(channel=peer, topics=[topic_id]))
        topics = getattr(res, "topics", None) or []
        if not topics:
            return None
        t = topics[0]
        return {
            "topic_id": t.id,
            "title": getattr(t, "title", None),
            "top_message_id": getattr(t, "top_message", None),
        }

    async def list_forum_topics(
        self, chat_id: int, limit: int = 20, query: str = ""
    ) -> dict[str, Any]:
        await self.require_forum_chat(chat_id)
        peer = await self.app.resolve_peer(chat_id)
        res = await self.app.invoke(
            GetForumTopics(
                channel=peer, offset_date=0, offset_id=0,
                offset_topic=0, limit=min(limit, 50), q=query or "",
            )
        )
        topics = []
        for t in getattr(res, "topics", []):
            topics.append({
                "topic_id": t.id,
                "title": getattr(t, "title", None),
                "top_message_id": getattr(t, "top_message", None),
            })
        return {"topics": topics}

    # --- Read context ---

    async def get_topic_context(
        self, chat_id: int, topic_id: int, limit: int = 30
    ) -> dict[str, Any]:
        meta = await self.get_topic_meta(chat_id, topic_id)
        if not meta:
            return {"error": "topic not found"}
        top_id = meta["top_message_id"]
        rows: list[dict] = []
        try:
            async for m in self.app.get_discussion_replies(chat_id, top_id, limit=limit):
                rows.append(serialize_message(m))
        except Exception:
            async for m in self.app.get_chat_history(chat_id, limit=400):
                if topic_message_matches(m, top_id):
                    rows.append(serialize_message(m))
                if len(rows) >= limit:
                    break
        rows.reverse()
        return {"topic": meta, "messages": rows}

    async def get_chat_context(
        self, chat_id: int, limit: int = 30
    ) -> dict[str, Any]:
        chat = await self.app.get_chat(chat_id)
        rows: list[dict] = []
        async for m in self.app.get_chat_history(chat_id, limit=limit):
            rows.append(serialize_message(m))
        rows.reverse()
        return {"chat": serialize_chat(chat), "messages": rows}

    # --- Search ---

    async def search_messages(
        self, chat_id: int, query: str, limit: int = 20, from_user: str | None = None
    ) -> dict[str, Any]:
        chat = await self.app.get_chat(chat_id)
        rows: list[dict] = []
        kwargs: dict[str, Any] = {"query": query, "limit": limit}
        if from_user:
            v = str(from_user).strip()
            if v.lstrip("-").isdigit():
                kwargs["from_user"] = int(v)
            else:
                kwargs["from_user"] = v if v.startswith("@") else f"@{v}"
        async for m in self.app.search_messages(chat_id, **kwargs):
            rows.append(serialize_message(m))
            if len(rows) >= limit:
                break
        return {"chat": serialize_chat(chat), "query": query, "messages": rows}

    async def search_messages_global(
        self, query: str, limit: int = 20, from_user: str | None = None,
        chat_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Search messages across indexed chats."""
        from resolver.chats import get_all_chat_ids

        candidate_chat_ids = chat_ids or await get_all_chat_ids()
        rows: list[dict] = []
        searched_chats = 0

        for chat_id in candidate_chat_ids:
            try:
                result = await self.search_messages(
                    chat_id=chat_id,
                    query=query,
                    limit=min(limit, 10),
                    from_user=from_user,
                )
            except Exception:
                continue

            searched_chats += 1
            rows.extend(result.get("messages", []))

        rows.sort(key=lambda item: item.get("date") or "", reverse=True)
        return {
            "query": query,
            "scope": "global",
            "searched_chats": searched_chats,
            "messages": rows[:limit],
        }

    # --- Send ---

    async def send_to_topic(
        self, chat_id: int, topic_id: int, text: str,
        reply_to_message_id: int | None = None,
        mention_username: str | None = None,
    ) -> dict[str, Any]:
        meta = await self.get_topic_meta(chat_id, topic_id)
        if not meta:
            return {"error": "topic not found"}
        mention = None
        if mention_username:
            v = str(mention_username).strip()
            if v:
                mention = v if v.startswith("@") else f"@{v}"
        outgoing = text
        if mention and mention.lower() not in outgoing.lower():
            outgoing = f"{mention} {outgoing}"
        target_reply = reply_to_message_id or meta["top_message_id"]
        msg = await self.app.send_message(
            chat_id=chat_id, text=outgoing, reply_to_message_id=target_reply,
        )
        return {
            "ok": True, "message_id": msg.id,
            "topic_id": topic_id, "mention_username": mention,
        }

    async def send_private_message(self, target: str, text: str) -> dict[str, Any]:
        normalized = normalize_target(target)
        chat = await self.app.get_chat(normalized)
        msg = await self.app.send_message(chat_id=chat.id, text=text)
        return {
            "ok": True, "message_id": msg.id,
            "chat_id": msg.chat.id, "target": str(normalized),
        }

    async def send_to_chat(
        self, chat_id: int, text: str, reply_to_message_id: int | None = None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        msg = await self.app.send_message(chat_id=chat_id, text=text, **kwargs)
        return {"ok": True, "message_id": msg.id, "chat_id": msg.chat.id}

    async def send_to_link(self, link: str, text: str) -> dict[str, Any]:
        from transport.link_parser import parse_telegram_link
        parsed = parse_telegram_link(link)
        chat = await self.app.get_chat(parsed["target"])
        if chat.type != "private" and not settings.is_allowed_chat(chat.id):
            raise ValueError(f"chat {chat.id} is not allowed")
        kwargs: dict[str, Any] = {}
        if parsed["reply_to_message_id"]:
            kwargs["reply_to_message_id"] = parsed["reply_to_message_id"]
        msg = await self.app.send_message(chat_id=chat.id, text=text, **kwargs)
        return {
            "ok": True, "message_id": msg.id, "chat_id": msg.chat.id,
            "target": parsed["target"],
        }

    # --- Forward / Pin ---

    async def forward_message(
        self, from_chat_id: int, message_id: int,
        to_chat_id: int, to_topic_id: int | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if to_topic_id:
            meta = await self.get_topic_meta(to_chat_id, to_topic_id)
            if meta:
                kwargs["reply_to_message_id"] = meta["top_message_id"]
        msg = await self.app.forward_messages(
            chat_id=to_chat_id, from_chat_id=from_chat_id,
            message_ids=message_id, **kwargs,
        )
        forwarded = msg[0] if isinstance(msg, list) else msg
        return {
            "ok": True, "message_id": forwarded.id,
            "from_chat_id": from_chat_id, "to_chat_id": to_chat_id,
        }

    async def pin_message(
        self, chat_id: int, message_id: int, both_sides: bool = True
    ) -> dict[str, Any]:
        await self.app.pin_chat_message(chat_id, message_id, both_sides=both_sides)
        return {"ok": True, "chat_id": chat_id, "message_id": message_id}

    # --- User info ---

    async def get_user_info(self, target: str) -> dict[str, Any]:
        normalized = normalize_target(target)
        users = await self.app.get_users(normalized)
        user = users[0] if isinstance(users, list) else users
        return {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": f"@{user.username}" if user.username else None,
            "is_bot": user.is_bot,
            "status": str(getattr(user, "status", "")),
        }

    # --- List chats / members ---

    async def list_available_chats(
        self, limit: int = 30, query: str = ""
    ) -> list[dict[str, Any]]:
        normalized_query = (query or "").strip().lower()
        rows: list[dict] = []
        if settings.allowed_chat_ids:
            for cid in sorted(settings.allowed_chat_ids):
                try:
                    chat = await self.app.get_chat(cid)
                    row = serialize_chat(chat)
                except Exception:
                    row = {"chat_id": cid, "error": "cannot access"}
                if normalized_query:
                    haystack = " ".join(
                        str(x).lower() for x in row.values() if x
                    )
                    if normalized_query not in haystack:
                        continue
                rows.append(row)
                if len(rows) >= limit:
                    break
            return rows

        async for dialog in self.app.get_dialogs(limit=200):
            chat = dialog.chat
            chat_type = str(getattr(chat, "type", ""))
            if chat_type in {"bot", "ChatType.BOT"}:
                continue
            row = serialize_chat(chat)
            if normalized_query:
                haystack = " ".join(str(x).lower() for x in row.values() if x)
                if normalized_query not in haystack:
                    continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    async def list_chat_members(
        self, chat_id: int, query: str = "", limit: int = 20
    ) -> dict[str, Any]:
        normalized_query = (query or "").strip().lower()
        rows: list[dict] = []
        async for member in self.app.get_chat_members(chat_id):
            user = getattr(member, "user", None)
            if not user:
                continue
            row = {
                "id": user.id,
                "name": " ".join(
                    x for x in [user.first_name, user.last_name] if x
                ).strip() or user.username or str(user.id),
                "username": f"@{user.username}" if user.username else None,
            }
            if normalized_query:
                haystack = " ".join(str(x).lower() for x in row.values() if x)
                if normalized_query not in haystack:
                    continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return {"chat_id": chat_id, "members": rows}

    async def list_topic_participants(
        self, chat_id: int, topic_id: int, query: str = "", limit: int = 20
    ) -> dict[str, Any]:
        meta = await self.get_topic_meta(chat_id, topic_id)
        if not meta:
            return {"error": "topic not found"}
        top_id = meta["top_message_id"]
        normalized_query = (query or "").strip().lower()
        participants: dict[str, dict[str, Any]] = {}
        messages: list[Message] = []
        try:
            async for m in self.app.get_discussion_replies(chat_id, top_id, limit=200):
                messages.append(m)
        except Exception:
            async for m in self.app.get_chat_history(chat_id, limit=400):
                if topic_message_matches(m, top_id):
                    messages.append(m)
        for m in messages:
            row = serialize_message(m)
            sender = row.get("sender")
            if not sender:
                continue
            key = str(sender.get("id"))
            if key not in participants:
                participants[key] = {
                    "sender": sender, "message_count": 0,
                    "last_message_id": row["id"],
                    "last_text": row["text"][:200],
                }
            participants[key]["message_count"] += 1
        rows = list(participants.values())
        if normalized_query:
            rows = [
                e for e in rows
                if normalized_query in " ".join(
                    str(v).lower() for v in (e.get("sender") or {}).values() if v
                )
            ]
        return {"topic": meta, "participants": rows[:limit]}
