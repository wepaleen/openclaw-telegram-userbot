"""Typed tool executor for the OpenClaw-backed Telegram manager runtime."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from apps.task_core.store.task_store import (
    cancel_scheduled_action,
    cancel_reminder,
    complete_task,
    create_reminder,
    create_scheduled_action,
    create_task,
    format_local_datetime,
    get_due_tasks,
    list_scheduled_actions,
    list_reminders,
    list_tasks,
    parse_datetime_input,
    update_task,
)
from apps.google_sheets.client import list_sheets as gs_list_sheets, read_spreadsheet as gs_read_spreadsheet
from apps.task_core.audit import Timer, list_audit_log, log_action
from apps.telethon_bridge.service import TelethonBridgeService
from config import normalize_chat_id, settings
from resolver.chats import search_chats, search_topics
from resolver.contacts import list_contacts, search_contacts
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
            with Timer() as timer:
                result = await self._execute_inner(name=name, args=args, event=event)
        except ToolExecutionError as e:
            result = {"error": str(e)}
            latency_ms = None
        except Exception as e:
            log.exception("Tool %s failed", name)
            result = {"error": f"{type(e).__name__}: {e}"}
            latency_ms = None
        else:
            latency_ms = timer.elapsed_ms

        await self._audit_tool_execution(
            name=name,
            args=args,
            event=event,
            result=result,
            latency_ms=latency_ms,
        )
        return result

    async def _execute_inner(
        self,
        *,
        name: str,
        args: dict[str, Any],
        event: InboundTelegramEvent,
    ) -> dict[str, Any]:
        if name == "list_available_chats":
            return await self._list_available_chats(limit=int(args.get("limit", 50)))
        if name == "list_contacts":
            return await self._list_contacts(args)
        if name == "add_contact":
            return await self._add_contact(args)
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
        if name == "list_reminders":
            return await self._list_reminders(args)
        if name == "inspect_delayed_items":
            return await self._inspect_delayed_items(args)
        if name == "schedule_action":
            return await self._schedule_action(event, args)
        if name == "list_scheduled_actions":
            return await self._list_scheduled_actions(args)
        if name == "list_audit_log":
            return await self._list_audit_log(args)
        if name == "cancel_scheduled_action":
            return await cancel_scheduled_action(int(args["scheduled_id"]))
        if name == "list_overdue_tasks":
            return await self._list_overdue_tasks(limit=int(args.get("limit", 20)))
        if name == "list_chat_members":
            return await self._list_chat_members(event, args)
        if name == "list_topic_participants":
            return await self._list_topic_participants(event, args)
        if name == "search_messages":
            return await self._search_messages(event, args)
        if name == "forward_message":
            return await self._forward_message(event, args)
        if name == "pin_message":
            return await self._pin_message(event, args)
        if name == "get_recent_context":
            return await self._get_recent_context(event, args)
        if name == "send_private_message":
            return await self._send_private_message(event, args)
        if name == "send_message":
            return await self._send_message(event, args)
        if name == "edit_message":
            return await self._edit_message(event, args)
        if name == "delete_message":
            return await self._delete_message(event, args)
        if name == "send_reaction":
            return await self._send_reaction(event, args)
        if name == "read_spreadsheet":
            return self._read_spreadsheet(args)
        if name == "list_sheets":
            return self._list_sheets(args)
        if name == "check_mention_limit":
            return await self._check_mention_limit(event, args)
        return {"error": f"unknown tool: {name}"}

    async def _list_available_chats(self, limit: int) -> dict[str, Any]:
        dialogs = await self.transport.list_dialogs(limit=limit)
        return {"chats": [_serialize_peer(peer) for peer in dialogs]}

    async def _list_contacts(self, args: dict[str, Any]) -> dict[str, Any]:
        query = self._as_str(args.get("query"))
        limit = int(args.get("limit", 50))
        rows = await list_contacts(limit=max(limit * 2, limit))
        if query:
            normalized = query.lower()
            rows = [
                row
                for row in rows
                if normalized in " ".join(
                    str(value).lower()
                    for value in [
                        row.get("display_name"),
                        row.get("username"),
                        row.get("user_id"),
                        row.get("aliases"),
                    ]
                    if value
                )
            ]
        return {"contacts": rows[:limit]}

    async def _add_contact(self, args: dict[str, Any]) -> dict[str, Any]:
        from resolver.contacts import add_contact
        display_name = str(args["display_name"]).strip()
        username = self._as_str(args.get("username"))
        if username:
            username = username.lstrip("@")
        aliases = args.get("aliases") or []
        notes = self._as_str(args.get("notes"))
        return await add_contact(
            display_name=display_name,
            username=username,
            aliases=aliases,
            notes=notes,
        )

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
            try:
                peer, source = await self._resolve_chat_peer(chat_query)
            except ToolExecutionError:
                # If ambiguous but one match is the current chat — prefer it
                if prefer_current_context and event.peer.title and chat_query.lower() in event.peer.title.lower():
                    peer = event.peer
                    source = "current_context"
                else:
                    raise
            top_msg_id = None
            is_topic_message = False
            if resolved_reply is None and prefer_current_context and peer.peer_id == event.peer.peer_id:
                resolved_reply = event.reply_to_msg_id

        if topic_query:
            if peer.peer_type == PeerType.USER:
                raise ToolExecutionError("нельзя искать topic context внутри личного диалога")
            topic = await self._resolve_topic(peer.peer_id, topic_query)
            top_msg_id = int(topic["topic_id"])
            is_topic_message = True
            source = "topic_index"
            if resolved_reply is None:
                resolved_reply = top_msg_id

        if resolved_reply is None and top_msg_id is None and prefer_current_context and peer.peer_id == event.peer.peer_id:
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

        chat_query = self._as_str(args.get("chat_query"))
        topic_query = self._as_str(args.get("topic_query"))

        if chat_query or topic_query:
            context = await self._resolve_target_context(
                event=event,
                chat_query=chat_query,
                topic_query=topic_query,
                reply_to_message_id=None,
                prefer_current_context=True,
            )
            target_peer = self._dict_to_peer(context["peer"])
            target_topic_id = context.get("top_msg_id")
        else:
            target_peer = event.peer
            target_topic_id = event.top_msg_id

        # Resolve mention target (who to tag when reminder fires)
        mention_username: str | None = None
        target_query = self._as_str(args.get("target_query"))
        if target_query:
            try:
                mention_peer, _ = await self._resolve_peer_query(
                    query=target_query, event=event,
                )
                mention_username = mention_peer.username
                if not mention_username:
                    mention_username = mention_peer.title
            except Exception:
                # Keep raw query as mention fallback
                mention_username = target_query

        result = await create_reminder(
            text=str(args["text"]),
            fire_at=parsed_time["remind_at_utc"],
            target_chat_id=target_peer.peer_id,
            target_topic_id=target_topic_id,
            target_user=target_peer.username if target_peer.peer_type == PeerType.USER else None,
            recurrence=self._as_str(args.get("recurrence")),
            source_sender_username=event.sender_username,
            mention_username=mention_username,
        )
        result["target_peer"] = _serialize_peer(target_peer)
        result["remind_at_local"] = parsed_time["remind_at_local"]
        result["timezone"] = parsed_time["timezone"]
        if mention_username:
            result["mention"] = mention_username
        return result

    async def _list_reminders(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", 20))
        status = self._as_str(args.get("status"))

        if status:
            reminders = await list_reminders(
                status=status,
                limit=limit,
            )
            for reminder in reminders:
                reminder["fire_at_local"] = format_local_datetime(reminder.get("fire_at"))
            return {"status": status, "reminders": reminders}

        statuses = ("pending", "fired", "cancelled")
        reminders_by_status: dict[str, list[dict[str, Any]]] = {}
        for item_status in statuses:
            rows = await list_reminders(status=item_status, limit=limit)
            for row in rows:
                row["fire_at_local"] = format_local_datetime(row.get("fire_at"))
            reminders_by_status[item_status] = rows

        return {
            "summary": {
                item_status: len(reminders_by_status[item_status])
                for item_status in statuses
            },
            "reminders_by_status": reminders_by_status,
        }

    async def _inspect_delayed_items(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", 10))
        reminder_statuses = ("pending", "fired", "cancelled")
        scheduled_statuses = ("pending", "executed", "failed", "cancelled")

        reminders_by_status: dict[str, list[dict[str, Any]]] = {}
        for status in reminder_statuses:
            rows = await list_reminders(status=status, limit=limit)
            for row in rows:
                row["fire_at_local"] = format_local_datetime(row.get("fire_at"))
            reminders_by_status[status] = rows

        actions_by_status: dict[str, list[dict[str, Any]]] = {}
        for status in scheduled_statuses:
            rows = await list_scheduled_actions(status=status, limit=limit)
            for row in rows:
                row["execute_at_local"] = format_local_datetime(row.get("execute_at"))
            actions_by_status[status] = rows

        audit_rows = await list_audit_log(limit=limit)
        delayed_audit = [
            row
            for row in audit_rows
            if str(row.get("action_type")) in {
                "set_reminder",
                "cancel_reminder",
                "schedule_action",
                "cancel_scheduled_action",
                "deliver_reminder",
                "list_reminders",
                "list_scheduled_actions",
                "inspect_delayed_items",
            }
        ]

        return {
            "reminders": reminders_by_status,
            "scheduled_actions": actions_by_status,
            "audit_log": delayed_audit[:limit],
        }

    async def _list_scheduled_actions(self, args: dict[str, Any]) -> dict[str, Any]:
        actions = await list_scheduled_actions(
            status=self._as_str(args.get("status")) or "pending",
            limit=int(args.get("limit", 20)),
        )
        for action in actions:
            action["execute_at_local"] = format_local_datetime(action.get("execute_at"))
        return {"scheduled_actions": actions}

    async def _list_audit_log(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = await list_audit_log(
            action_type=self._as_str(args.get("action_type")),
            success=args.get("success") if "success" in args else None,
            limit=int(args.get("limit", 20)),
        )
        return {"audit_log": rows}

    async def _schedule_action(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        parsed_time = self._parse_time(
            time_phrase=str(args["time_phrase"]),
            timezone_name=settings.bot_timezone,
        )

        requested_action_type = self._as_str(args.get("action_type"))
        text = str(args["text"])

        if self._as_str(args.get("target_query")):
            target_peer, source_context = await self._resolve_peer_query(
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
            source_context = context.get("source", "current_context")

        if requested_action_type in {"run_agent", "agent_prompt"}:
            action_type = "run_agent"
            action_params = {
                "prompt": text,
                "target_peer": _serialize_peer(target_peer),
                "reply_to_message_id": reply_to_msg_id,
                "top_msg_id": top_msg_id,
                "source_sender_id": event.sender_id,
                "source_sender_username": event.sender_username,
                "source_message_id": event.message_id,
            }
        elif target_peer.peer_type == PeerType.USER:
            action_type = requested_action_type or "send_private"
            action_params = {
                "target": target_peer.username or target_peer.peer_id,
                "text": text,
            }
        elif top_msg_id:
            action_type = requested_action_type or "send_topic"
            action_params = {
                "chat_id": target_peer.peer_id,
                "top_msg_id": top_msg_id,
                "reply_to_message_id": reply_to_msg_id or top_msg_id,
                "text": text,
            }
        else:
            action_type = requested_action_type or "send_message"
            action_params = {
                "chat_id": target_peer.peer_id,
                "reply_to_message_id": reply_to_msg_id,
                "text": text,
            }

        if action_type not in {"send_message", "send_chat", "send_private", "send_topic", "run_agent"}:
            raise ToolExecutionError(
                "schedule_action пока поддерживает только send_message, send_chat, send_private, send_topic и run_agent"
            )

        recurrence = self._as_str(args.get("recurrence"))
        result = await create_scheduled_action(
            action_type=action_type,
            action_params=action_params,
            execute_at=parsed_time["remind_at_utc"],
            source_chat_id=event.peer.peer_id,
            source_message_id=event.message_id,
            recurrence=recurrence,
        )
        result["execute_at_local"] = parsed_time["remind_at_local"]
        result["timezone"] = parsed_time["timezone"]
        result["action_type"] = action_type
        result["action_params"] = action_params
        result["source"] = source_context
        return result

    async def _list_overdue_tasks(self, limit: int) -> dict[str, Any]:
        now_utc = datetime.now(timezone.utc).isoformat()
        tasks = await get_due_tasks(now_utc)
        return {"tasks": tasks[:limit]}

    async def _list_chat_members(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if self._as_str(args.get("chat_query")):
            peer, source = await self._resolve_chat_peer(str(args["chat_query"]))
        else:
            peer = event.peer
            source = "current_context"

        if peer.peer_type == PeerType.USER:
            raise ToolExecutionError("в личном диалоге нет списка участников")

        members = await self.transport.list_chat_members(
            peer=peer,
            query=self._as_str(args.get("query")) or "",
            limit=int(args.get("limit", 50)),
        )
        return {
            "peer": _serialize_peer(peer),
            "source": source,
            "members": members,
        }

    async def _list_topic_participants(
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
        top_msg_id = context.get("top_msg_id")
        if not top_msg_id:
            raise ToolExecutionError("не удалось определить тему для list_topic_participants")

        peer = self._dict_to_peer(context["peer"])
        participants = await self.transport.list_topic_participants(
            peer=peer,
            top_msg_id=int(top_msg_id),
            query=self._as_str(args.get("query")) or "",
            limit=int(args.get("limit", 20)),
        )
        return {
            "context": context,
            "participants": participants,
        }

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

    async def _forward_message(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        source_peer = event.peer
        source = "current_context"
        if self._as_str(args.get("from_chat_query")):
            source_peer, source = await self._resolve_chat_peer(str(args["from_chat_query"]))

        message_id = self._as_int(args.get("message_id")) or event.reply_to_msg_id
        if message_id is None:
            raise ToolExecutionError(
                "для forward_message нужен message_id или reply на сообщение"
            )

        if self._as_str(args.get("target_query")):
            target_peer, target_source = await self._resolve_peer_query(
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
            target_source = context.get("source", "current_context")

        result = await self.transport.forward_message(
            source_peer=source_peer,
            message_id=int(message_id),
            target_peer=target_peer,
            reply_to_msg_id=reply_to_msg_id,
            top_msg_id=top_msg_id,
            drop_author=bool(args.get("drop_author", False)),
        )
        result["source_peer"] = _serialize_peer(source_peer)
        result["target_peer"] = _serialize_peer(target_peer)
        result["source"] = source
        result["target_source"] = target_source
        return result

    async def _pin_message(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if self._as_str(args.get("chat_query")):
            peer, source = await self._resolve_chat_peer(str(args["chat_query"]))
        else:
            peer = event.peer
            source = "current_context"

        message_id = self._as_int(args.get("message_id")) or event.reply_to_msg_id
        if message_id is None:
            raise ToolExecutionError("для pin_message нужен message_id или reply на сообщение")

        result = await self.transport.pin_message(
            peer=peer,
            message_id=int(message_id),
            notify=bool(args.get("notify", False)),
        )
        result["peer"] = _serialize_peer(peer)
        result["source"] = source
        return result

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
            # Resolve topic context if topic_query is provided alongside target_query
            top_msg_id = None
            if self._as_str(args.get("topic_query")) and target_peer.peer_type != PeerType.USER:
                topic = await self._resolve_topic(target_peer.peer_id, str(args["topic_query"]))
                top_msg_id = int(topic["topic_id"])
                if reply_to_msg_id is None:
                    reply_to_msg_id = top_msg_id
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

        # Track @mentions in group chats for mention limiting
        if target_peer.peer_type != PeerType.USER:
            await self._track_mentions_in_text(text, target_peer.peer_id, top_msg_id)

        return result

    async def _edit_message(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        message_id = int(args["message_id"])
        text = str(args["text"])
        chat_query = self._as_str(args.get("chat_query"))
        if chat_query:
            peer, _ = await self._resolve_chat_peer(chat_query)
        else:
            peer = event.peer
        return await self.transport.edit_message(peer, message_id=message_id, text=text)

    async def _delete_message(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        raw_ids = args.get("message_ids", [])
        if isinstance(raw_ids, int):
            raw_ids = [raw_ids]
        message_ids = [int(mid) for mid in raw_ids]
        if not message_ids:
            raise ToolExecutionError("message_ids не может быть пустым")
        chat_query = self._as_str(args.get("chat_query"))
        if chat_query:
            peer, _ = await self._resolve_chat_peer(chat_query)
        else:
            peer = event.peer
        revoke = bool(args.get("revoke", True))
        return await self.transport.delete_messages(peer, message_ids=message_ids, revoke=revoke)

    async def _send_reaction(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        message_id = self._as_int(args.get("message_id"))
        if message_id is None:
            message_id = event.reply_to_msg_id or event.message_id
        if not message_id:
            raise ToolExecutionError("не указан message_id и нет reply контекста")
        emoticon = str(args.get("emoticon", "👍"))
        chat_query = self._as_str(args.get("chat_query"))
        if chat_query:
            peer, _ = await self._resolve_chat_peer(chat_query)
        else:
            peer = event.peer
        return await self.transport.send_reaction(peer, message_id=message_id, emoticon=emoticon)

    @staticmethod
    def _read_spreadsheet(args: dict[str, Any]) -> dict[str, Any]:
        spreadsheet = str(args["spreadsheet"])
        sheet_name = args.get("sheet_name")
        range_a1 = args.get("range")
        limit = int(args.get("limit", 100))
        return gs_read_spreadsheet(
            spreadsheet,
            sheet_name=sheet_name,
            range_a1=range_a1,
            limit=limit,
        )

    @staticmethod
    def _list_sheets(args: dict[str, Any]) -> dict[str, Any]:
        return gs_list_sheets(str(args["spreadsheet"]))

    async def _send_private_message(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        target_peer, source = await self._resolve_peer_query(
            query=str(args["target_query"]),
            event=event,
        )
        if target_peer.peer_type != PeerType.USER:
            raise ToolExecutionError(
                "send_private_message можно использовать только для личного диалога с человеком"
            )

        result = await self.transport.send(
            OutboundTelegramCommand(
                target_peer=target_peer,
                text=str(args["text"]),
                idempotency_key=f"{event.event_id}:send_private_message",
            )
        )
        result["target_peer"] = _serialize_peer(target_peer)
        result["source"] = source
        return result

    _MENTION_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]{3,31})")

    async def _check_mention_limit(
        self,
        event: InboundTelegramEvent,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Check if the bot should switch to DM instead of tagging in chat."""
        from apps.task_core.store.mention_tracker import check_mention_limit
        username = str(args["username"]).lstrip("@")
        chat_id = self._as_int(args.get("chat_id")) or event.peer.peer_id
        topic_id = self._as_int(args.get("topic_id")) or event.top_msg_id
        return await check_mention_limit(
            mentioned_username=username,
            chat_id=chat_id,
            topic_id=topic_id,
        )

    async def _track_mentions_in_text(
        self,
        text: str,
        chat_id: int,
        topic_id: int | None,
    ) -> None:
        """Extract @username mentions from text and record them in mention_tracker."""
        mentions = self._MENTION_RE.findall(text)
        if not mentions:
            return
        try:
            from apps.task_core.store.mention_tracker import record_mention
            for username in set(mentions):
                await record_mention(
                    mentioned_username=username,
                    chat_id=chat_id,
                    topic_id=topic_id,
                )
        except Exception as e:
            log.warning("Failed to track mentions: %s", e)

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

        # Extract numeric ID from "Name (123456)" pattern (LLM often does this)
        id_in_parens = re.search(r"\((\d{5,})\)\s*$", value)
        if id_in_parens:
            chat_id = normalize_chat_id(id_in_parens.group(1))
            return await self.transport.resolve_peer_ref(chat_id), "chat_id"

        db_matches = await search_chats(value)
        if len(db_matches) == 1:
            return await self.transport.resolve_peer_ref(int(db_matches[0]["chat_id"])), "chat_index"
        if len(db_matches) > 1:
            # Prefer the forum (supergroup with topics) over a plain chat
            forums = [m for m in db_matches if m.get("is_forum")]
            if len(forums) == 1:
                return await self.transport.resolve_peer_ref(int(forums[0]["chat_id"])), "chat_index"
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

    async def _audit_tool_execution(
        self,
        *,
        name: str,
        args: dict[str, Any],
        event: InboundTelegramEvent,
        result: dict[str, Any],
        latency_ms: int | None,
    ) -> None:
        target_chat_id, target_topic_id = self._extract_audit_target(result)
        await log_action(
            action_type=name,
            intent=event.text,
            source_chat_id=event.peer.peer_id,
            source_message_id=event.message_id,
            source_user_id=event.sender_id,
            target_chat_id=target_chat_id,
            target_topic_id=target_topic_id,
            params=args,
            result=result,
            success=not bool(result.get("error")),
            error=result.get("error"),
            llm_used=True,
            latency_ms=latency_ms,
        )

    @classmethod
    def _extract_audit_target(cls, result: dict[str, Any]) -> tuple[int | None, int | None]:
        peer = result.get("target_peer") or result.get("peer")
        if isinstance(peer, dict) and peer.get("peer_id") is not None:
            return int(peer["peer_id"]), cls._as_int(result.get("top_msg_id"))

        context = result.get("context")
        if isinstance(context, dict):
            context_peer = context.get("peer")
            if isinstance(context_peer, dict) and context_peer.get("peer_id") is not None:
                return int(context_peer["peer_id"]), cls._as_int(context.get("top_msg_id"))

        action_params = result.get("action_params")
        if isinstance(action_params, dict):
            return cls._as_int(action_params.get("chat_id")), cls._as_int(
                action_params.get("top_msg_id")
            )

        return None, None

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
