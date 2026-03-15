"""Typed tool executor for the OpenClaw-backed Telegram manager runtime."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apps.task_core.store.task_store import (
    cancel_reminder,
    complete_task,
    create_reminder,
    create_task,
    format_local_datetime,
    get_due_tasks,
    list_tasks,
    parse_datetime_input,
    update_task,
)
from apps.telethon_bridge.service import TelethonBridgeService
from config import normalize_chat_id, settings
from resolver.chats import search_chats, search_topics
from resolver.contacts import search_contacts
from shared.schemas.telegram import (
    InboundTelegramEvent,
    OutboundTelegramCommand,
    PeerRef,
    PeerType,
)

log = logging.getLogger("openclaw_adapter.tool_executor")


class ToolExecutionError(ValueError):
    """Raised when a typed tool cannot be resolved or executed safely."""


def _serialize_peer(peer: PeerRef) -> dict[str, Any]:
    return {
        "peer_type": peer.peer_type.value,
        "peer_id": peer.peer_id,
        "access_hash": peer.access_hash,
        "username": peer.username,
        "title": peer.title,
    }


def _peer_label(peer: PeerRef) -> str:
    return peer.title or (f"@{peer.username}" if peer.username else str(peer.peer_id))


class OpenClawToolExecutor:
    """Executes typed OpenClaw tools against Telethon transport and task core."""

    def __init__(
        self,
        *,
        transport: TelethonBridgeService,
        dialogs_limit: int = 100,
        context_limit: int = 20,
    ) -> None:
        self.transport = transport
        self.dialogs_limit = dialogs_limit
        self.context_limit = context_limit

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        event: InboundTelegramEvent,
    ) -> dict[str, Any]:
        try:
            if name == "list_available_chats":
                return await self._list_available_chats(limit=int(args.get("limit", 50)))
            if name == "resolve_recipient":
                return await self._resolve_recipient(
                    query=str(args["query"]),
                    event=event,
                )
            if name == "resolve_target_context":
                return await self._resolve_target_context(
                    event=event,
                    chat_query=self._as_str(args.get("chat_query")),
                    topic_query=self._as_str(args.get("topic_query")),
                    reply_to_message_id=self._as_int(args.get("reply_to_message_id")),
                    prefer_current_context=bool(args.get("prefer_current_context", True)),
                )
            if name == "parse_time":
                return self._parse_time(
                    time_phrase=str(args["time_phrase"]),
                    timezone_name=self._as_str(args.get("timezone")),
                )
            if name == "create_task":
                return await self._create_task(event, args)
            if name == "update_task":
                return await self._update_task(args)
            if name == "list_tasks":
                return await self._list_tasks(args)
            if name == "complete_task":
                return await complete_task(int(args["task_id"]))
            if name == "set_reminder":
                return await self._set_reminder(event, args)
            if name == "cancel_reminder":
                return await cancel_reminder(int(args["reminder_id"]))
            if name == "list_overdue_tasks":
                return await self._list_overdue_tasks(limit=int(args.get("limit", 20)))
            if name == "search_messages":
                return await self._search_messages(event, args)
            if name == "get_recent_context":
                return await self._get_recent_context(event, args)
            if name == "send_message":
                return await self._send_message(event, args)
            return {"error": f"unknown tool: {name}"}
        except ToolExecutionError as e:
            return {"error": str(e)}
        except Exception as e:
            log.exception("Tool %s failed", name)
            return {"error": f"{type(e).__name__}: {e}"}

    async def _list_available_chats(self, limit: int) -> dict[str, Any]:
        dialogs = await self.transport.list_dialogs(limit=limit)
        return {"chats": [_serialize_peer(peer) for peer in dialogs]}

    async def _resolve_recipient(
        self,
        *,
        query: str,
        event: InboundTelegramEvent,
    ) -> dict[str, Any]:
        peer, source = await self._resolve_peer_query(query=query, event=event)
        return {
            "peer": _serialize_peer(peer),
            "display_label": _peer_label(peer),
            "source": source,
            "needs_confirmation": False,
        }

    async def _resolve_target_context(
        self,
        *,
        event: InboundTelegramEvent,
        chat_query: str | None,
        topic_query: str | None,
        reply_to_message_id: int | None,
        prefer_current_context: bool,
    ) -> dict[str, Any]:
        peer = event.peer
        source = "current_context"
        top_msg_id = event.top_msg_id if prefer_current_context else None
        resolved_reply = reply_to_message_id
        is_topic_message = bool(top_msg_id)

        if chat_query:
            peer, source = await self._resolve_chat_peer(chat_query)
            top_msg_id = None
            is_topic_message = False
            if resolved_reply is None and prefer_current_context and peer.peer_id == event.peer.peer_id:
                resolved_reply = event.reply_to_msg_id

        if topic_query:
            if peer.peer_type == PeerType.USER:
                raise ToolExecutionError("нельзя искать topic context внутри личного диалога")
            topic = await self._resolve_topic(peer.peer_id, topic_query)
            top_msg_id = int(topic["top_message_id"])
            is_topic_message = True
            source = "topic_index"
            if resolved_reply is None:
                resolved_reply = top_msg_id

        if resolved_reply is None and top_msg_id is None and prefer_current_context:
            resolved_reply = event.reply_to_msg_id

        return {
            "peer": _serialize_peer(peer),
            "reply_to_msg_id": resolved_reply,
            "top_msg_id": top_msg_id,
            "is_topic_message": is_topic_message,
            "source": source,
        }

    def _parse_time(self, *, time_phrase: str, timezone_name: str | None) -> dict[str, Any]:
        parsed_utc = parse_datetime_input(time_phrase)
        if not parsed_utc:
            raise ToolExecutionError(f"не удалось разобрать время: {time_phrase}")
        return {
            "original_text": time_phrase,
            "remind_at_local": format_local_datetime(parsed_utc),
            "remind_at_utc": parsed_utc,
            "timezone": timezone_name or settings.bot_timezone,
            "confidence": 1.0,
            "requires_confirmation": False,
        }

    async def _create_task(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        assignee = None
        if self._as_str(args.get("assignee_query")):
            recipient = await self._resolve_recipient(
                query=str(args["assignee_query"]),
                event=event,
            )
            assignee = recipient["display_label"]

        due_at = None
        parsed_due: dict[str, Any] | None = None
        if self._as_str(args.get("due_phrase")):
            parsed_due = self._parse_time(
                time_phrase=str(args["due_phrase"]),
                timezone_name=settings.bot_timezone,
            )
            due_at = parsed_due["remind_at_utc"]

        result = await create_task(
            title=str(args["title"]),
            description=self._as_str(args.get("description")),
            assignee=assignee,
            due_at=due_at,
            source_chat_id=event.peer.peer_id,
            source_message_id=event.message_id,
        )
        if parsed_due:
            result["due_at_local"] = parsed_due["remind_at_local"]
            result["due_at_utc"] = parsed_due["remind_at_utc"]
        if assignee:
            result["assignee"] = assignee
        return result

    async def _update_task(self, args: dict[str, Any]) -> dict[str, Any]:
        update_fields: dict[str, Any] = {}
        for key in ("status", "title", "description"):
            if key in args and args[key] is not None:
                update_fields[key] = args[key]

        if self._as_str(args.get("assignee_query")):
            update_fields["assignee"] = str(args["assignee_query"])

        if self._as_str(args.get("due_phrase")):
            parsed_due = self._parse_time(
                time_phrase=str(args["due_phrase"]),
                timezone_name=settings.bot_timezone,
            )
            update_fields["due_at"] = parsed_due["remind_at_utc"]

        if not update_fields:
            raise ToolExecutionError("нет полей для обновления задачи")
        return await update_task(int(args["task_id"]), **update_fields)

    async def _list_tasks(self, args: dict[str, Any]) -> dict[str, Any]:
        tasks = await list_tasks(
            status=self._as_str(args.get("status")),
            assignee=self._as_str(args.get("assignee_query")),
            limit=int(args.get("limit", 20)),
        )
        return {"tasks": tasks}

    async def _set_reminder(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        parsed_time = self._parse_time(
            time_phrase=str(args["time_phrase"]),
            timezone_name=settings.bot_timezone,
        )

        target_peer = event.peer
        target_topic_id = event.top_msg_id
        if self._as_str(args.get("target_query")):
            target_peer, _ = await self._resolve_peer_query(
                query=str(args["target_query"]),
                event=event,
            )
            if target_peer.peer_id != event.peer.peer_id:
                target_topic_id = None

        result = await create_reminder(
            text=str(args["text"]),
            fire_at=parsed_time["remind_at_utc"],
            target_chat_id=target_peer.peer_id,
            target_topic_id=target_topic_id,
            target_user=target_peer.username,
        )
        result["target_peer"] = _serialize_peer(target_peer)
        result["remind_at_local"] = parsed_time["remind_at_local"]
        result["timezone"] = parsed_time["timezone"]
        return result

    async def _list_overdue_tasks(self, limit: int) -> dict[str, Any]:
        now_utc = datetime.now(timezone.utc).isoformat()
        tasks = await get_due_tasks(now_utc)
        return {"tasks": tasks[:limit]}

    async def _search_messages(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        query = str(args["query"])
        limit = int(args.get("limit", 20))
        from_peer = None
        if self._as_str(args.get("from_query")):
            from_peer, _ = await self._resolve_peer_query(
                query=str(args["from_query"]),
                event=event,
            )

        if self._as_str(args.get("chat_query")):
            peer, _ = await self._resolve_chat_peer(str(args["chat_query"]))
            messages = await self.transport.search_messages(
                peer=peer,
                query=query,
                limit=limit,
                from_peer=from_peer,
            )
            return {
                "query": query,
                "scope": "chat",
                "peer": _serialize_peer(peer),
                "messages": messages,
            }

        dialogs = await self.transport.list_dialogs(limit=self.dialogs_limit)
        rows: list[dict[str, Any]] = []
        searched = 0
        per_chat_limit = max(1, min(limit, 10))
        for peer in dialogs:
            try:
                messages = await self.transport.search_messages(
                    peer=peer,
                    query=query,
                    limit=per_chat_limit,
                    from_peer=from_peer,
                )
            except Exception:
                continue
            searched += 1
            rows.extend(messages)

        rows.sort(key=lambda item: item.get("date_utc") or "", reverse=True)
        return {
            "query": query,
            "scope": "global",
            "searched_peers": searched,
            "messages": rows[:limit],
        }

    async def _get_recent_context(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        context = await self._resolve_target_context(
            event=event,
            chat_query=self._as_str(args.get("chat_query")),
            topic_query=self._as_str(args.get("topic_query")),
            reply_to_message_id=None,
            prefer_current_context=True,
        )
        peer = self._dict_to_peer(context["peer"])
        messages = await self.transport.get_recent_context(
            peer=peer,
            limit=int(args.get("limit", self.context_limit)),
            top_msg_id=context.get("top_msg_id"),
            reply_to_msg_id=context.get("reply_to_msg_id"),
        )
        return {"context": context, "messages": messages}

    async def _send_message(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        text = str(args["text"])
        if self._as_str(args.get("target_query")):
            target_peer, source = await self._resolve_peer_query(
                query=str(args["target_query"]),
                event=event,
            )
            reply_to_msg_id = self._as_int(args.get("reply_to_message_id"))
            top_msg_id = None
        else:
            context = await self._resolve_target_context(
                event=event,
                chat_query=self._as_str(args.get("chat_query")),
                topic_query=self._as_str(args.get("topic_query")),
                reply_to_message_id=self._as_int(args.get("reply_to_message_id")),
                prefer_current_context=True,
            )
            target_peer = self._dict_to_peer(context["peer"])
            reply_to_msg_id = context.get("reply_to_msg_id")
            top_msg_id = context.get("top_msg_id")
            source = context.get("source", "current_context")

        result = await self.transport.send(
            OutboundTelegramCommand(
                target_peer=target_peer,
                text=text,
                reply_to_msg_id=reply_to_msg_id,
                top_msg_id=top_msg_id,
                idempotency_key=f"{event.event_id}:send_message",
            )
        )
        result["target_peer"] = _serialize_peer(target_peer)
        result["source"] = source
        return result

    async def _resolve_peer_query(
        self,
        *,
        query: str,
        event: InboundTelegramEvent,
    ) -> tuple[PeerRef, str]:
        value = query.strip()
        if not value:
            raise ToolExecutionError("пустой запрос для resolve_recipient")

        if self._matches_peer(event.peer, value):
            return event.peer, "current_context"

        if value.startswith("@"):
            return await self.transport.resolve_peer_ref(value), "direct_input"
        if value.lstrip("-").isdigit():
            raw_id = int(value)
            try:
                return await self.transport.resolve_peer_ref(raw_id), "direct_input"
            except Exception:
                return await self.transport.resolve_peer_ref(normalize_chat_id(value)), "direct_input"

        contact_matches = await search_contacts(value)
        if len(contact_matches) == 1:
            contact = contact_matches[0]
            target = contact.get("username") or contact.get("user_id")
            if not target:
                raise ToolExecutionError(
                    f"контакт «{contact['display_name']}» найден, но у него нет username или id"
                )
            return await self.transport.resolve_peer_ref(target), "contact_book"
        if len(contact_matches) > 1:
            variants = ", ".join(
                c.get("display_name") or c.get("username") or str(c.get("id"))
                for c in contact_matches[:5]
            )
            raise ToolExecutionError(f"контакт «{value}» неоднозначен: {variants}")

        return await self._resolve_chat_peer(value)

    async def _resolve_chat_peer(self, query: str) -> tuple[PeerRef, str]:
        value = query.strip()
        if not value:
            raise ToolExecutionError("пустой запрос для resolve_target_context")

        if value.lstrip("-").isdigit():
            chat_id = normalize_chat_id(value)
            return await self.transport.resolve_peer_ref(chat_id), "chat_id"

        db_matches = await search_chats(value)
        if len(db_matches) == 1:
            return await self.transport.resolve_peer_ref(int(db_matches[0]["chat_id"])), "chat_index"
        if len(db_matches) > 1:
            variants = ", ".join(
                f"{match.get('title') or match['chat_id']} ({match['chat_id']})"
                for match in db_matches[:5]
            )
            raise ToolExecutionError(f"чат «{value}» неоднозначен: {variants}")

        dialogs = await self.transport.list_dialogs(limit=self.dialogs_limit)
        exact = [peer for peer in dialogs if self._matches_peer(peer, value, exact_only=True)]
        if len(exact) == 1:
            return exact[0], "dialogs_exact"
        if len(exact) > 1:
            variants = ", ".join(_peer_label(peer) for peer in exact[:5])
            raise ToolExecutionError(f"чат «{value}» неоднозначен: {variants}")

        partial = [peer for peer in dialogs if self._matches_peer(peer, value)]
        if len(partial) == 1:
            return partial[0], "dialogs_partial"
        if len(partial) > 1:
            variants = ", ".join(_peer_label(peer) for peer in partial[:5])
            raise ToolExecutionError(f"чат «{value}» неоднозначен: {variants}")

        raise ToolExecutionError(f"чат или диалог «{value}» не найден")

    async def _resolve_topic(self, chat_id: int, query: str) -> dict[str, Any]:
        matches = await search_topics(chat_id, query)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            variants = ", ".join(
                f"{match.get('title') or match['topic_id']} ({match['topic_id']})"
                for match in matches[:5]
            )
            raise ToolExecutionError(f"тема «{query}» неоднозначна: {variants}")
        raise ToolExecutionError(f"тема «{query}» не найдена")

    @staticmethod
    def _matches_peer(peer: PeerRef, query: str, exact_only: bool = False) -> bool:
        value = query.strip().lower()
        usernames = []
        if peer.username:
            usernames.extend([peer.username.lower(), f"@{peer.username.lower().lstrip('@')}"])
        titles = [peer.title.lower()] if peer.title else []
        ids = [str(peer.peer_id)]
        haystack = usernames + titles + ids
        if exact_only:
            return value in haystack
        return any(value in item for item in haystack)

    @staticmethod
    def _dict_to_peer(raw: dict[str, Any]) -> PeerRef:
        return PeerRef(
            peer_type=PeerType(str(raw["peer_type"])),
            peer_id=int(raw["peer_id"]),
            access_hash=raw.get("access_hash"),
            username=raw.get("username"),
            title=raw.get("title"),
        )

    @staticmethod
    def _as_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        raw = str(value).strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
        return None
