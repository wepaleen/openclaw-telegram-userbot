"""Telethon + OpenClaw runtime entrypoint for the new userbot manager stack."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

import httpx

from apps.openclaw_adapter import (
    OpenClawAdapterService,
    OpenClawToolExecutor,
)
from apps.task_core.db import close_db, get_db
from apps.task_core.scheduler import start_scheduler, stop_scheduler
from apps.telethon_bridge.index_sync import sync_all_indexes
from apps.telethon_bridge.service import TelethonBridgeService
from config import settings
from shared.schemas.telegram import InboundTelegramEvent, OutboundTelegramCommand, PeerType

log = logging.getLogger("telethon_manager_runtime")


class TelethonOpenClawRuntime:
    """Always-on runtime that binds Telethon transport to OpenClaw and task core."""

    def __init__(
        self,
        *,
        transport: TelethonBridgeService | None = None,
        tool_executor: OpenClawToolExecutor | None = None,
        adapter: OpenClawAdapterService | None = None,
        scheduler_interval: int = 30,
    ) -> None:
        self.transport = transport or TelethonBridgeService()
        self.tool_executor = tool_executor or OpenClawToolExecutor(
            transport=self.transport,
        )
        self.adapter = adapter or OpenClawAdapterService(
            transport=self.transport,
            execute_tool=self.tool_executor.execute,
        )
        self.scheduler_interval = scheduler_interval
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.transport.on_event(self.handle_event)

    async def initialize(self) -> None:
        await get_db()
        try:
            await sync_all_indexes(self.transport)
        except Exception as e:
            log.warning("Initial Telethon index sync failed: %s", e)
        start_scheduler(
            self._scheduler_send,
            self._scheduler_execute,
            interval=self.scheduler_interval,
        )
        log.info("Telethon/OpenClaw runtime initialized")

    async def shutdown(self) -> None:
        stop_scheduler()
        try:
            await self.transport.stop()
        finally:
            await close_db()
        log.info("Telethon/OpenClaw runtime stopped")

    async def run(self) -> None:
        try:
            await self.transport.start()
            await self.initialize()
            await self.transport.run_forever()
        finally:
            await self.shutdown()

    async def handle_event(self, event: InboundTelegramEvent) -> None:
        normalized_text = self._normalize_event_text(event)
        if not normalized_text:
            return

        event.text = normalized_text
        lock = self._locks[event.session_key]
        async with lock:
            try:
                result = await self.adapter.handle_event(event)
                if result.text.strip():
                    await self._reply_text(event, result.text)
            except httpx.HTTPStatusError as e:
                body = e.response.text[:1000] if e.response is not None else ""
                await self._reply_text(
                    event,
                    f"OpenClaw вернул HTTP {e.response.status_code if e.response else '?'}:\n{body}",
                )
            except Exception as e:
                log.exception("Runtime failed for event %s", event.event_id)
                await self._reply_text(
                    event,
                    f"Ошибка runtime: {type(e).__name__}: {e}",
                )

    def _normalize_event_text(self, event: InboundTelegramEvent) -> str | None:
        text = (event.text or "").strip()
        if not text:
            return None

        if event.peer.peer_type == PeerType.USER:
            return text

        if text.startswith(settings.group_trigger):
            stripped = text[len(settings.group_trigger):].strip()
            return stripped or None

        return None

    async def _reply_text(self, event: InboundTelegramEvent, text: str) -> None:
        parts = [text[i:i + 3500] for i in range(0, len(text), 3500)] or ["(пустой ответ)"]
        first = True
        for part in parts:
            if first:
                await self.transport.send(
                    OutboundTelegramCommand(
                        target_peer=event.peer,
                        text=part,
                        reply_to_msg_id=event.message_id,
                        top_msg_id=event.top_msg_id if event.is_topic_message else None,
                    )
                )
                first = False
                continue

            await self.transport.send(
                OutboundTelegramCommand(
                    target_peer=event.peer,
                    text=part,
                    reply_to_msg_id=event.top_msg_id if event.is_topic_message else None,
                    top_msg_id=event.top_msg_id if event.is_topic_message else None,
                )
            )

    async def _scheduler_send(
        self,
        *,
        chat_id: int,
        text: str,
        topic_id: int | None = None,
    ) -> None:
        peer = await self.transport.resolve_peer_ref(chat_id)
        await self.transport.send(
            OutboundTelegramCommand(
                target_peer=peer,
                text=text,
                reply_to_msg_id=topic_id,
                top_msg_id=topic_id,
                idempotency_key=f"scheduler:send:{chat_id}:{topic_id or 0}:{hash(text)}",
            )
        )

    async def _scheduler_execute(self, action_type: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if action_type in {"send_message", "send_chat"}:
                chat_id = params.get("chat_id") or params.get("target_chat_id")
                if chat_id is None:
                    return {"error": "scheduled send_message requires chat_id"}
                peer = await self.transport.resolve_peer_ref(int(chat_id))
                return await self.transport.send(
                    OutboundTelegramCommand(
                        target_peer=peer,
                        text=str(params["text"]),
                        reply_to_msg_id=self._as_int(params.get("reply_to_message_id")),
                        top_msg_id=self._as_int(params.get("top_msg_id")),
                        idempotency_key=f"scheduler:action:{action_type}:{params.get('idempotency_key', '')}",
                    )
                )

            if action_type == "send_private":
                target = params.get("target")
                if target is None:
                    return {"error": "scheduled send_private requires target"}
                peer = await self.transport.resolve_peer_ref(target)
                return await self.transport.send(
                    OutboundTelegramCommand(
                        target_peer=peer,
                        text=str(params["text"]),
                        idempotency_key=f"scheduler:action:send_private:{params.get('idempotency_key', '')}",
                    )
                )

            if action_type == "send_topic":
                chat_id = params.get("chat_id")
                top_msg_id = self._as_int(params.get("topic_id")) or self._as_int(params.get("top_msg_id"))
                if chat_id is None or top_msg_id is None:
                    return {"error": "scheduled send_topic requires chat_id and topic_id"}
                peer = await self.transport.resolve_peer_ref(int(chat_id))
                reply_to = self._as_int(params.get("reply_to_message_id")) or top_msg_id
                return await self.transport.send(
                    OutboundTelegramCommand(
                        target_peer=peer,
                        text=str(params["text"]),
                        reply_to_msg_id=reply_to,
                        top_msg_id=top_msg_id,
                        idempotency_key=f"scheduler:action:send_topic:{params.get('idempotency_key', '')}",
                    )
                )

            return {"error": f"unsupported scheduled action: {action_type}"}
        except Exception as e:
            log.exception("Scheduled action failed: %s", action_type)
            return {"error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        raw = str(value).strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
        return None
