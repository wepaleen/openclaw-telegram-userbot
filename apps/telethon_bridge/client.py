"""Async Telethon transport used by the future OpenClaw bridge."""

import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl import types

from apps.telethon_bridge.errors import PeerResolutionError, SessionNotAuthorizedError
from apps.telethon_bridge.serializers import (
    normalize_new_message_event,
    peer_ref_from_entity,
    serialize_dialog_entity,
    serialize_member_entity,
    serialize_message,
)
from config import settings
from shared.schemas.telegram import InboundTelegramEvent, OutboundTelegramCommand, PeerRef, PeerType

log = logging.getLogger("telethon_bridge.client")

InboundHandler = Callable[[InboundTelegramEvent], Awaitable[None]]


class TelethonBridgeClient:
    """Thin async wrapper around Telethon for MTProto userbot transport."""

    def __init__(
        self,
        *,
        account_id: str = "default",
        session_name: str | None = None,
        string_session: str | None = None,
        api_id: int | None = None,
        api_hash: str | None = None,
    ) -> None:
        self.account_id = account_id
        self.session_name = session_name or settings.telethon_session_name
        self.string_session = string_session or settings.telethon_string_session
        self.api_id = api_id or settings.api_id
        self.api_hash = api_hash or settings.api_hash
        self.client = self._build_client()
        self._handlers: list[InboundHandler] = []
        self._event_handler_registered = False

    def _build_client(self) -> TelegramClient:
        session: str | StringSession
        if self.string_session:
            session = StringSession(self.string_session)
        else:
            session = self.session_name

        return TelegramClient(
            session,
            self.api_id,
            self.api_hash,
            device_model=settings.telethon_device_model,
            system_version=settings.telethon_system_version,
            app_version=settings.telethon_app_version,
            lang_code=settings.telethon_lang_code,
            system_lang_code=settings.telethon_system_lang_code,
        )

    async def connect(self) -> None:
        """Connect and verify that the user session is authorized."""
        if self.client.is_connected():
            return
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise SessionNotAuthorizedError(
                "Telethon session is not authorized. Login the account first."
            )

    async def disconnect(self) -> None:
        if self.client.is_connected():
            await self.client.disconnect()

    async def run_forever(self) -> None:
        """Connect, install inbound handlers and run until disconnected."""
        await self.connect()
        self._ensure_event_handler()
        await self.client.run_until_disconnected()

    def add_inbound_handler(self, handler: InboundHandler) -> None:
        """Register a coroutine that receives normalized inbound events."""
        self._handlers.append(handler)
        self._ensure_event_handler()

    def _ensure_event_handler(self) -> None:
        if self._event_handler_registered:
            return
        self.client.add_event_handler(self._on_new_message, events.NewMessage(incoming=True))
        self._event_handler_registered = True

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        try:
            chat = await event.get_chat()
        except Exception as e:
            log.warning("Failed to fetch chat for incoming event: %s", e)
            return

        chat_id = int(getattr(chat, "id", 0))
        is_private = isinstance(chat, types.User)
        if not settings.is_allowed_chat(chat_id) and not is_private:
            return

        normalized = await normalize_new_message_event(
            event,
            account_id=self.account_id,
            chat_entity=chat,
        )

        for handler in list(self._handlers):
            try:
                await handler(normalized)
            except Exception as e:
                log.exception("Inbound handler failed: %s", e)

    async def list_dialogs(self, limit: int = 100) -> list[PeerRef]:
        """Return visible dialogs as shared peer references."""
        rows: list[PeerRef] = []
        async for dialog in self.client.iter_dialogs(limit=limit):
            entity = dialog.entity
            if getattr(entity, "bot", False):
                continue
            chat_id = int(getattr(entity, "id", 0))
            if not settings.is_allowed_chat(chat_id) and not isinstance(entity, types.User):
                continue
            rows.append(peer_ref_from_entity(entity))
        return rows

    async def list_dialog_rows(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return visible dialogs with metadata suitable for chat/topic indexing."""
        rows: list[dict[str, Any]] = []
        async for dialog in self.client.iter_dialogs(limit=limit):
            entity = dialog.entity
            if getattr(entity, "bot", False):
                continue
            chat_id = int(getattr(entity, "id", 0))
            if not settings.is_allowed_chat(chat_id) and not isinstance(entity, types.User):
                continue
            rows.append(serialize_dialog_entity(entity))
        return rows

    async def resolve_input_peer(self, peer: PeerRef | str | int) -> Any:
        """Resolve a shared peer reference into a Telethon input entity."""
        if isinstance(peer, PeerRef):
            if peer.access_hash is not None:
                if peer.peer_type == PeerType.USER:
                    return types.InputPeerUser(peer.peer_id, peer.access_hash)
                if peer.peer_type == PeerType.CHANNEL:
                    return types.InputPeerChannel(peer.peer_id, peer.access_hash)
            if peer.peer_type == PeerType.CHAT:
                return types.InputPeerChat(peer.peer_id)
            if peer.username:
                return await self.client.get_input_entity(peer.username)
            return await self.client.get_input_entity(peer.peer_id)

        if isinstance(peer, str):
            value = peer.strip()
            # Ensure usernames are prefixed with @ for Telethon resolution
            if value and not value.startswith("@") and not value.lstrip("-").isdigit():
                value = f"@{value}"
            return await self.client.get_input_entity(value)

        return await self.client.get_input_entity(peer)

    async def get_entity(self, peer: PeerRef | str | int) -> Any:
        input_peer = await self.resolve_input_peer(peer)
        return await self.client.get_entity(input_peer)

    async def resolve_peer_ref(self, peer: PeerRef | str | int) -> PeerRef:
        """Resolve any supported peer reference into the shared PeerRef model."""
        entity = await self.get_entity(peer)
        return peer_ref_from_entity(entity)

    async def send_command(self, command: OutboundTelegramCommand) -> dict[str, Any]:
        """Execute an outbound Telegram send/reply command."""
        input_peer = await self.resolve_input_peer(command.target_peer)
        entity = await self.client.get_entity(input_peer)

        if command.top_msg_id is not None and command.top_msg_id != 1:
            # Always use explicit MTProto topic context for forum threads
            message = await self._send_with_topic_context(input_peer, command)
        else:
            reply_target = command.reply_to_msg_id or command.top_msg_id
            message = await self.client.send_message(
                entity=input_peer,
                message=command.text,
                reply_to=reply_target,
                parse_mode=command.parse_mode,
                link_preview=not command.disable_link_preview,
            )

        serialized = serialize_message(message, chat_entity=entity)
        serialized["target_peer"] = command.target_peer
        serialized["idempotency_key"] = command.idempotency_key
        return serialized

    async def _send_with_topic_context(
        self,
        input_peer: Any,
        command: OutboundTelegramCommand,
    ) -> Any:
        """Send a message with explicit topic context for MTProto forum threads."""
        result = await self.client(
            functions.messages.SendMessageRequest(
                peer=input_peer,
                message=command.text,
                no_webpage=command.disable_link_preview,
                random_id=random.randrange(1, 2**63 - 1),
                reply_to=types.InputReplyToMessage(
                    reply_to_msg_id=command.reply_to_msg_id or command.top_msg_id,
                    top_msg_id=command.top_msg_id,
                ),
            )
        )
        return self._extract_message_from_updates(
            result,
            action_name="send_message",
        )

    async def search_messages(
        self,
        peer: PeerRef | str | int,
        query: str,
        limit: int = 20,
        from_peer: PeerRef | str | int | None = None,
    ) -> list[dict[str, Any]]:
        """Search messages inside a peer."""
        input_peer = await self.resolve_input_peer(peer)
        entity = await self.client.get_entity(input_peer)

        kwargs: dict[str, Any] = {"limit": limit, "search": query}
        if from_peer is not None:
            kwargs["from_user"] = await self.resolve_input_peer(from_peer)

        rows: list[dict[str, Any]] = []
        async for message in self.client.iter_messages(input_peer, **kwargs):
            rows.append(serialize_message(message, chat_entity=entity))
            if len(rows) >= limit:
                break
        return rows

    async def get_recent_context(
        self,
        peer: PeerRef | str | int,
        *,
        limit: int = 30,
        top_msg_id: int | None = None,
        reply_to_msg_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent messages, optionally filtered to a reply/topic context."""
        input_peer = await self.resolve_input_peer(peer)
        entity = await self.client.get_entity(input_peer)

        rows: list[dict[str, Any]] = []
        async for message in self.client.iter_messages(input_peer, limit=max(limit * 4, 100)):
            if top_msg_id and not self._message_matches_topic(message, top_msg_id):
                continue
            if reply_to_msg_id and not self._message_matches_reply(message, reply_to_msg_id):
                continue
            rows.append(serialize_message(message, chat_entity=entity))
            if len(rows) >= limit:
                break
        rows.reverse()
        return rows

    async def list_chat_members(
        self,
        peer: PeerRef | str | int,
        *,
        query: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List participants in a group/channel peer."""
        input_peer = await self.resolve_input_peer(peer)
        entity = await self.client.get_entity(input_peer)
        if isinstance(entity, types.User):
            raise PeerResolutionError("cannot list members in a private dialog")

        normalized_query = query.strip().lower()
        rows: list[dict[str, Any]] = []
        async for participant in self.client.iter_participants(input_peer):
            row = serialize_member_entity(participant)
            haystack = " ".join(
                str(value).lower()
                for value in [row["id"], row["name"], row["username"]]
                if value
            )
            if normalized_query and normalized_query not in haystack:
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    async def list_topic_participants(
        self,
        peer: PeerRef | str | int,
        *,
        top_msg_id: int,
        query: str = "",
        limit: int = 20,
        history_limit: int = 400,
    ) -> list[dict[str, Any]]:
        """List distinct participants and their latest messages inside a topic."""
        input_peer = await self.resolve_input_peer(peer)
        entity = await self.client.get_entity(input_peer)
        normalized_query = query.strip().lower()
        participants: dict[str, dict[str, Any]] = {}

        async for message in self.client.iter_messages(input_peer, limit=history_limit):
            if not self._message_matches_topic(message, top_msg_id):
                continue

            sender = getattr(message, "sender", None)
            if sender is None and getattr(message, "sender_id", None) is not None:
                try:
                    sender = await message.get_sender()
                except Exception:
                    sender = None
            if sender is None or getattr(sender, "id", None) is None:
                continue

            sender_key = str(int(getattr(sender, "id")))
            if sender_key not in participants:
                participants[sender_key] = {
                    "member": serialize_member_entity(sender),
                    "message_count": 0,
                    "last_message_id": int(message.id),
                    "last_message_date": (
                        message.date.isoformat() if getattr(message, "date", None) else None
                    ),
                    "last_text": (message.message or "").strip()[:200],
                }
            participants[sender_key]["message_count"] += 1

        rows = list(participants.values())
        if normalized_query:
            rows = [
                row
                for row in rows
                if normalized_query in " ".join(
                    str(value).lower()
                    for value in [
                        row["member"].get("id"),
                        row["member"].get("name"),
                        row["member"].get("username"),
                        row.get("last_text"),
                    ]
                    if value
                )
            ]
        return rows[:limit]

    async def forward_message(
        self,
        *,
        source_peer: PeerRef | str | int,
        message_id: int,
        target_peer: PeerRef | str | int,
        reply_to_msg_id: int | None = None,
        top_msg_id: int | None = None,
        drop_author: bool = False,
    ) -> dict[str, Any]:
        """Forward a message to another peer, optionally into a topic/reply context."""
        input_source = await self.resolve_input_peer(source_peer)
        input_target = await self.resolve_input_peer(target_peer)
        target_entity = await self.client.get_entity(input_target)

        reply_to = None
        if reply_to_msg_id is not None:
            reply_to = types.InputReplyToMessage(
                reply_to_msg_id=reply_to_msg_id,
                top_msg_id=top_msg_id,
            )

        result = await self.client(
            functions.messages.ForwardMessagesRequest(
                from_peer=input_source,
                id=[int(message_id)],
                random_id=[random.randrange(1, 2**63 - 1)],
                to_peer=input_target,
                top_msg_id=top_msg_id,
                reply_to=reply_to,
                drop_author=drop_author,
            )
        )

        message = self._extract_message_from_updates(
            result,
            action_name="forward_message",
        )
        serialized = serialize_message(message, chat_entity=target_entity)
        serialized["target_peer"] = peer_ref_from_entity(target_entity)
        serialized["source_message_id"] = int(message_id)
        serialized["drop_author"] = drop_author
        return serialized

    async def pin_message(
        self,
        peer: PeerRef | str | int,
        *,
        message_id: int,
        notify: bool = False,
    ) -> dict[str, Any]:
        """Pin a message inside a Telegram chat/channel."""
        input_peer = await self.resolve_input_peer(peer)
        entity = await self.client.get_entity(input_peer)
        await self.client.pin_message(
            entity=input_peer,
            message=int(message_id),
            notify=notify,
        )
        return {
            "ok": True,
            "message_id": int(message_id),
            "peer": peer_ref_from_entity(entity),
            "notify": notify,
        }

    async def list_forum_topics(
        self,
        peer: PeerRef | str | int,
        *,
        limit: int = 50,
        query: str = "",
    ) -> dict[str, Any]:
        """Fetch forum topics for a channel/forum peer through MTProto raw requests."""
        entity = await self.get_entity(peer)
        if not getattr(entity, "forum", False):
            raise PeerResolutionError(f'chat "{getattr(entity, "title", entity.id)}" is not a forum')

        access_hash = getattr(entity, "access_hash", None)
        if access_hash is None:
            raise PeerResolutionError("forum chat has no access_hash for MTProto channel request")

        result = await self.client(
            functions.channels.GetForumTopicsRequest(
                channel=types.InputChannel(channel_id=int(entity.id), access_hash=access_hash),
                offset_date=datetime.fromtimestamp(0, tz=timezone.utc),
                offset_id=0,
                offset_topic=0,
                limit=min(limit, 100),
                q=query or "",
            )
        )

        topics = []
        for topic in getattr(result, "topics", []) or []:
            topics.append(
                {
                    "topic_id": int(topic.id),
                    "title": getattr(topic, "title", None),
                    "top_message_id": getattr(topic, "top_message", None),
                }
            )
        return {"topics": topics}

    @staticmethod
    def _message_matches_topic(message: Any, top_msg_id: int) -> bool:
        reply = getattr(message, "reply_to", None)
        reply_top = getattr(reply, "reply_to_top_id", None)
        reply_id = getattr(reply, "reply_to_msg_id", None)
        return bool(
            message.id == top_msg_id
            or reply_top == top_msg_id
            or reply_id == top_msg_id
        )

    @staticmethod
    def _message_matches_reply(message: Any, reply_to_msg_id: int) -> bool:
        reply = getattr(message, "reply_to", None)
        reply_id = getattr(reply, "reply_to_msg_id", None)
        return bool(message.id == reply_to_msg_id or reply_id == reply_to_msg_id)

    @staticmethod
    def _extract_message_from_updates(result: Any, *, action_name: str) -> Any:
        for update in getattr(result, "updates", []):
            if isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)):
                return update.message
        raise PeerResolutionError(
            f"{action_name} succeeded but the created message could not be extracted"
        )
