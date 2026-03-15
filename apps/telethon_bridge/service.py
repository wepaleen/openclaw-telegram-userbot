"""Service wrapper around the Telethon transport client."""

from apps.telethon_bridge.client import InboundHandler, TelethonBridgeClient
from shared.schemas.telegram import InboundTelegramEvent, OutboundTelegramCommand, PeerRef


class TelethonBridgeService:
    """High-level service facade for the future OpenClaw adapter."""

    def __init__(self, client: TelethonBridgeClient | None = None) -> None:
        self.client = client or TelethonBridgeClient()
        self._handlers: list[InboundHandler] = []
        self.client.add_inbound_handler(self._dispatch)

    def on_event(self, handler: InboundHandler) -> None:
        """Register a normalized inbound event handler."""
        self._handlers.append(handler)

    async def start(self) -> None:
        await self.client.connect()

    async def stop(self) -> None:
        await self.client.disconnect()

    async def run_forever(self) -> None:
        await self.client.run_forever()

    async def send(self, command: OutboundTelegramCommand) -> dict:
        return await self.client.send_command(command)

    async def list_dialogs(self, limit: int = 100) -> list[PeerRef]:
        return await self.client.list_dialogs(limit=limit)

    async def list_dialog_rows(self, limit: int = 100) -> list[dict]:
        return await self.client.list_dialog_rows(limit=limit)

    async def resolve_peer_ref(self, peer: PeerRef | str | int) -> PeerRef:
        return await self.client.resolve_peer_ref(peer)

    async def list_forum_topics(
        self,
        peer: PeerRef | str | int,
        *,
        limit: int = 50,
        query: str = "",
    ) -> dict:
        return await self.client.list_forum_topics(peer, limit=limit, query=query)

    async def search_messages(
        self,
        peer: PeerRef | str | int,
        query: str,
        *,
        limit: int = 20,
        from_peer: PeerRef | str | int | None = None,
    ) -> list[dict]:
        return await self.client.search_messages(
            peer=peer,
            query=query,
            limit=limit,
            from_peer=from_peer,
        )

    async def list_chat_members(
        self,
        peer: PeerRef | str | int,
        *,
        query: str = "",
        limit: int = 50,
    ) -> list[dict]:
        return await self.client.list_chat_members(
            peer=peer,
            query=query,
            limit=limit,
        )

    async def list_topic_participants(
        self,
        peer: PeerRef | str | int,
        *,
        top_msg_id: int,
        query: str = "",
        limit: int = 20,
        history_limit: int = 400,
    ) -> list[dict]:
        return await self.client.list_topic_participants(
            peer=peer,
            top_msg_id=top_msg_id,
            query=query,
            limit=limit,
            history_limit=history_limit,
        )

    async def get_recent_context(
        self,
        peer: PeerRef | str | int,
        *,
        limit: int = 30,
        top_msg_id: int | None = None,
        reply_to_msg_id: int | None = None,
    ) -> list[dict]:
        return await self.client.get_recent_context(
            peer=peer,
            limit=limit,
            top_msg_id=top_msg_id,
            reply_to_msg_id=reply_to_msg_id,
        )

    async def forward_message(
        self,
        *,
        source_peer: PeerRef | str | int,
        message_id: int,
        target_peer: PeerRef | str | int,
        reply_to_msg_id: int | None = None,
        top_msg_id: int | None = None,
        drop_author: bool = False,
    ) -> dict:
        return await self.client.forward_message(
            source_peer=source_peer,
            message_id=message_id,
            target_peer=target_peer,
            reply_to_msg_id=reply_to_msg_id,
            top_msg_id=top_msg_id,
            drop_author=drop_author,
        )

    async def pin_message(
        self,
        peer: PeerRef | str | int,
        *,
        message_id: int,
        notify: bool = False,
    ) -> dict:
        return await self.client.pin_message(
            peer=peer,
            message_id=message_id,
            notify=notify,
        )

    async def edit_message(
        self,
        peer: PeerRef | str | int,
        *,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
    ) -> dict:
        return await self.client.edit_message(peer, message_id=message_id, text=text, parse_mode=parse_mode)

    async def delete_messages(
        self,
        peer: PeerRef | str | int,
        *,
        message_ids: list[int],
        revoke: bool = True,
    ) -> dict:
        return await self.client.delete_messages(peer, message_ids=message_ids, revoke=revoke)

    async def send_reaction(
        self,
        peer: PeerRef | str | int,
        *,
        message_id: int,
        emoticon: str = "👍",
    ) -> dict:
        return await self.client.send_reaction(peer, message_id=message_id, emoticon=emoticon)

    async def set_typing(
        self,
        peer: PeerRef | str | int,
        *,
        typing: bool = True,
        top_msg_id: int | None = None,
    ) -> None:
        await self.client.set_typing(peer, typing=typing, top_msg_id=top_msg_id)

    async def _dispatch(self, event: InboundTelegramEvent) -> None:
        for handler in list(self._handlers):
            await handler(event)
