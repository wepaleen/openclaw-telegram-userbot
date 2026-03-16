"""Service layer that binds Telethon transport to the OpenClaw runtime."""

import logging
from collections.abc import AsyncIterator
from typing import Any

from apps.openclaw_adapter.runtime import AgentRunResult, OpenClawAgentRuntime, ToolExecutor
from apps.telethon_bridge.service import TelethonBridgeService
from resolver.chats import get_forum_chat_ids, get_topics_for_chat
from shared.schemas.telegram import InboundTelegramEvent, PeerRef

log = logging.getLogger("openclaw_adapter.service")


class OpenClawAdapterService:
    """Bridge service that prepares context and delegates reasoning to OpenClaw."""

    def __init__(
        self,
        *,
        transport: TelethonBridgeService,
        execute_tool: ToolExecutor,
        runtime: OpenClawAgentRuntime | None = None,
        context_limit: int = 12,
        dialogs_limit: int = 50,
    ) -> None:
        self.transport = transport
        self.execute_tool = execute_tool
        self.runtime = runtime or OpenClawAgentRuntime()
        self.context_limit = context_limit
        self.dialogs_limit = dialogs_limit

    async def handle_event(self, event: InboundTelegramEvent) -> AgentRunResult:
        recent_context = await self.transport.get_recent_context(
            peer=event.peer,
            limit=self.context_limit,
            top_msg_id=event.top_msg_id,
            reply_to_msg_id=event.reply_to_msg_id if not event.top_msg_id else None,
        )
        dialogs = await self.transport.list_dialogs(limit=self.dialogs_limit)
        available_chats = await self._build_chats_with_topics(dialogs)
        return await self.runtime.run(
            event=event,
            recent_context=recent_context,
            available_chats=available_chats,
            execute_tool=self.execute_tool,
        )

    def can_stream(self, event: InboundTelegramEvent) -> bool:
        """Check if this event can use streaming (DM only, conversational, no tools)."""
        if self.runtime.chat_client is None:
            return False
        if self.runtime._needs_tools(event.text):
            return False
        # Only stream in DMs — groups/topics have stricter rate limits
        from shared.schemas.telegram import PeerType
        if event.peer.peer_type != PeerType.USER:
            return False
        return True

    async def stream_event(self, event: InboundTelegramEvent) -> AsyncIterator[str]:
        """Stream conversational response chunks for an event.

        Yields text deltas. Caller is responsible for assembling and sending them.
        """
        recent_context = await self.transport.get_recent_context(
            peer=event.peer,
            limit=self.context_limit,
            top_msg_id=event.top_msg_id,
            reply_to_msg_id=event.reply_to_msg_id if not event.top_msg_id else None,
        )
        dialogs = await self.transport.list_dialogs(limit=self.dialogs_limit)
        available_chats = await self._build_chats_with_topics(dialogs)
        async for chunk in self.runtime.stream(
            event=event,
            recent_context=recent_context,
            available_chats=available_chats,
        ):
            yield chunk

    async def _build_chats_with_topics(
        self, dialogs: list[PeerRef]
    ) -> list[dict[str, Any]]:
        """Serialize dialogs and attach indexed topics for forum chats."""
        forum_chat_ids = set(await get_forum_chat_ids())
        result: list[dict[str, Any]] = []
        for peer in dialogs:
            entry = self._serialize_peer(peer)
            if peer.peer_id in forum_chat_ids:
                entry["is_forum"] = True
                try:
                    topics = await get_topics_for_chat(peer.peer_id)
                    if topics:
                        entry["topics"] = [
                            {"title": t["title"], "topic_id": t["topic_id"]}
                            for t in topics
                        ]
                except Exception as e:
                    log.warning("Failed to load topics for chat %d: %s", peer.peer_id, e)
            result.append(entry)
        return result

    @staticmethod
    def _serialize_peer(peer: PeerRef) -> dict[str, Any]:
        return {
            "peer_type": peer.peer_type.value,
            "peer_id": peer.peer_id,
            "access_hash": peer.access_hash,
            "username": peer.username,
            "title": peer.title,
        }
