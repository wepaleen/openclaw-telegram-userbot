"""Service layer that binds Telethon transport to the OpenClaw runtime."""

from dataclasses import asdict
from typing import Any

from apps.openclaw_adapter.runtime import AgentRunResult, OpenClawAgentRuntime, ToolExecutor
from apps.telethon_bridge.service import TelethonBridgeService
from shared.schemas.telegram import InboundTelegramEvent, PeerRef


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
        available_chats = [self._serialize_peer(peer) for peer in dialogs]
        return await self.runtime.run(
            event=event,
            recent_context=recent_context,
            available_chats=available_chats,
            execute_tool=self.execute_tool,
        )

    @staticmethod
    def _serialize_peer(peer: PeerRef) -> dict[str, Any]:
        return {
            "peer_type": peer.peer_type.value,
            "peer_id": peer.peer_id,
            "access_hash": peer.access_hash,
            "username": peer.username,
            "title": peer.title,
        }
