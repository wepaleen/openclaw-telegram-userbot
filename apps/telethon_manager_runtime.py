"""Telethon + OpenClaw runtime entrypoint for the new userbot manager stack."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from collections import defaultdict
from typing import Any

import httpx

from apps.rate_limit import rate_limiter
from apps.telethon_bridge.formatting import md_to_tg_html
from apps.openclaw_adapter import (
    OpenClawAdapterService,
    OpenClawToolExecutor,
)
from apps.task_core.db import close_db, get_db
from apps.task_core.scheduler import start_scheduler, stop_scheduler
from apps.telethon_bridge.index_sync import sync_all_indexes
from apps.telethon_bridge.service import TelethonBridgeService
from config import settings
from shared.schemas.telegram import InboundTelegramEvent, OutboundTelegramCommand, PeerRef, PeerType

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
            sync_fn=lambda: sync_all_indexes(self.transport),
            sync_every=120,  # every 120 ticks × 30s = ~1 hour
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
        # Auto-resolve mention tracker when someone sends a message in a chat
        if event.sender_username and event.peer.peer_type != PeerType.USER:
            try:
                from apps.task_core.store.mention_tracker import resolve_mentions_by_sender
                await resolve_mentions_by_sender(
                    sender_username=event.sender_username,
                    chat_id=event.peer.peer_id,
                    topic_id=event.top_msg_id,
                )
            except Exception as e:
                log.debug("mention_tracker resolve failed: %s", e)

        normalized_text = self._normalize_event_text(event)
        if not normalized_text:
            return

        event.text = normalized_text

        # Rate limit check (admins exempt)
        if event.sender_id and event.sender_id not in settings.admin_user_ids:
            allowed, retry_after = rate_limiter.check(event.sender_id)
            if not allowed:
                log.info("Rate limited user %s (retry in %.1fs)", event.sender_id, retry_after)
                await self._reply_text(event, f"Слишком много запросов. Подожди {int(retry_after)} сек.")
                return

        # Instant 👀 — acknowledge the message was seen
        try:
            await self.transport.send_reaction(
                event.peer, message_id=event.message_id, emoticon="👀",
            )
        except Exception as e:
            log.warning("Failed to send 👀 reaction: %s", e)

        # Show "typing..." indicator while processing
        try:
            await self.transport.set_typing(
                event.peer, typing=True, top_msg_id=event.top_msg_id,
            )
        except Exception:
            pass

        reaction: str | None = None
        lock = self._locks[event.session_key]
        async with lock:
            try:
                result = await self.adapter.handle_event(event)
                # Strip leading "..." placeholder that some models prepend
                reply_text = result.text.strip()
                if reply_text.startswith("..."):
                    reply_text = reply_text.lstrip(".").strip()
                if reply_text:
                    await self._reply_text(event, reply_text)
                reaction = self._pick_reaction(event.text, result.text, result.tool_rounds)
            except httpx.HTTPStatusError as e:
                body = e.response.text[:1000] if e.response is not None else ""
                await self._reply_text(
                    event,
                    f"OpenClaw вернул HTTP {e.response.status_code if e.response else '?'}:\n{body}",
                )
                reaction = "💔"
            except Exception as e:
                log.exception("Runtime failed for event %s", event.event_id)
                await self._reply_text(
                    event,
                    f"Ошибка runtime: {type(e).__name__}: {e}",
                )
                reaction = "💔"

        # Replace 👀 with context-aware reaction after response
        if reaction:
            try:
                await self.transport.send_reaction(
                    event.peer, message_id=event.message_id, emoticon=reaction,
                )
            except Exception as e:
                log.debug("Could not set reaction %s: %s", reaction, e)

    def _normalize_event_text(self, event: InboundTelegramEvent) -> str | None:
        text = (event.text or "").strip()
        if not text:
            return None

        # DMs — always respond
        if event.peer.peer_type == PeerType.USER:
            return text

        # Groups / supergroups / forum topics:
        # 1) Trigger name (case-insensitive) — strip it from text
        trigger = settings.group_trigger
        if text.lower().startswith(trigger.lower()):
            stripped = text[len(trigger):].strip().lstrip(",").strip()
            return stripped or None

        # 2) Explicit reply to a bot's own message in a topic
        if (
            event.is_topic_message
            and event.reply_to_msg_id
            and event.reply_to_msg_id != event.top_msg_id
            and event.reply_to_sender_id == self.transport.client.self_id
        ):
            return text

        return None

    @staticmethod
    def _pick_reaction(user_text: str, bot_reply: str, tool_rounds: int) -> str:
        """Choose a context-aware reaction emoji based on input/output.

        Only uses emojis from the Telegram allowed reactions set.
        """
        low_user = user_text.lower()
        low_reply = bot_reply.lower()

        # Greetings
        if any(w in low_user for w in ("привет", "здравств", "добр", "hello", "hi ", "хай")):
            return "🤝"

        # Thanks
        if any(w in low_user for w in ("спасибо", "благодар", "thanks", "thx")):
            return "❤️"

        # Task/reminder done
        if any(w in low_reply for w in (
            "напоминание установлено", "задача создана", "напоминание отменено",
            "задача выполнена", "сообщение отправлено", "удален",
        )):
            return "🔥"

        # Used tools (action performed)
        if tool_rounds > 0:
            return "⚡"

        # Conversational reply
        return "👌"

    async def _reply_text(self, event: InboundTelegramEvent, text: str) -> None:
        await self._send_text(
            target_peer=event.peer,
            text=text,
            first_reply_to=event.message_id,
            followup_reply_to=event.top_msg_id if event.is_topic_message else None,
            top_msg_id=event.top_msg_id if event.is_topic_message else None,
        )

    async def _send_text(
        self,
        *,
        target_peer: PeerRef,
        text: str,
        first_reply_to: int | None = None,
        followup_reply_to: int | None = None,
        top_msg_id: int | None = None,
        idempotency_prefix: str = "runtime:send",
    ) -> None:
        html = md_to_tg_html(text)
        parts = [html[i:i + 3500] for i in range(0, len(html), 3500)] or ["(пустой ответ)"]
        first = True
        for part in parts:
            if first:
                await self.transport.send(
                    OutboundTelegramCommand(
                        target_peer=target_peer,
                        text=part,
                        reply_to_msg_id=first_reply_to,
                        top_msg_id=top_msg_id,
                        parse_mode="html",
                        idempotency_key=f"{idempotency_prefix}:first:{hash(part)}",
                    )
                )
                first = False
                continue

            await self.transport.send(
                OutboundTelegramCommand(
                    target_peer=target_peer,
                    text=part,
                    reply_to_msg_id=followup_reply_to,
                    top_msg_id=top_msg_id,
                    parse_mode="html",
                    idempotency_key=f"{idempotency_prefix}:next:{hash(part)}",
                )
            )

    async def _scheduler_send(
        self,
        *,
        chat_id: int,
        target: str | int | None = None,
        text: str,
        topic_id: int | None = None,
    ) -> None:
        resolve_target: str | int = chat_id
        if target is not None:
            if isinstance(target, str) and target and not target.startswith("@"):
                resolve_target = f"@{target}"
            else:
                resolve_target = target
        log.info(
            "scheduler_send: chat_id=%s target=%s resolve_target=%s topic_id=%s text=%s",
            chat_id, target, resolve_target, topic_id, text[:80],
        )
        peer = await self.transport.resolve_peer_ref(resolve_target)
        log.info(
            "scheduler_send: resolved peer=%s (type=%s, id=%s, username=%s)",
            peer, peer.peer_type.value, peer.peer_id, peer.username,
        )
        result = await self.transport.send(
            OutboundTelegramCommand(
                target_peer=peer,
                text=text,
                reply_to_msg_id=topic_id,
                top_msg_id=topic_id,
                idempotency_key=f"scheduler:send:{chat_id}:{topic_id or 0}:{hash(text)}",
            )
        )
        log.info("scheduler_send: send result=%s", result)

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

            if action_type == "run_agent":
                return await self._scheduler_run_agent(params)

            return {"error": f"unsupported scheduled action: {action_type}"}
        except Exception as e:
            log.exception("Scheduled action failed: %s", action_type)
            return {"error": f"{type(e).__name__}: {e}"}

    async def _scheduler_run_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        target_peer_raw = params.get("target_peer")
        if isinstance(target_peer_raw, dict):
            target_peer = self._dict_to_peer(target_peer_raw)
        else:
            target = params.get("target")
            chat_id = params.get("chat_id")
            if target is not None:
                target_peer = await self.transport.resolve_peer_ref(target)
            elif chat_id is not None:
                target_peer = await self.transport.resolve_peer_ref(int(chat_id))
            else:
                return {"error": "scheduled run_agent requires target_peer, target or chat_id"}

        prompt = str(params.get("prompt") or params.get("text") or "").strip()
        if not prompt:
            return {"error": "scheduled run_agent requires prompt"}

        reply_to_msg_id = self._as_int(params.get("reply_to_message_id"))
        top_msg_id = self._as_int(params.get("top_msg_id"))
        source_message_id = self._as_int(params.get("source_message_id")) or reply_to_msg_id or 0

        event = InboundTelegramEvent(
            event_id=(
                f"scheduler:{target_peer.peer_type.value}:{target_peer.peer_id}:"
                f"{top_msg_id or reply_to_msg_id or 0}:{int(datetime.now(timezone.utc).timestamp())}"
            ),
            account_id="scheduler",
            peer=target_peer,
            sender_id=self._as_int(params.get("source_sender_id")),
            sender_username=params.get("source_sender_username"),
            message_id=source_message_id,
            text=prompt,
            date_utc=datetime.now(timezone.utc),
            reply_to_msg_id=reply_to_msg_id,
            top_msg_id=top_msg_id,
            is_topic_message=bool(top_msg_id),
            raw_context_ref=str(target_peer.peer_id),
            metadata={"scheduled": "1"},
        )

        lock = self._locks[event.session_key]
        async with lock:
            result = await self.adapter.handle_event(event, max_tool_rounds=30)

        if result.text.strip():
            await self._send_text(
                target_peer=target_peer,
                text=result.text,
                first_reply_to=reply_to_msg_id or top_msg_id,
                followup_reply_to=top_msg_id,
                top_msg_id=top_msg_id,
                idempotency_prefix=(
                    f"scheduler:run_agent:{target_peer.peer_type.value}:{target_peer.peer_id}"
                ),
            )

        return {
            "ok": True,
            "result_text": result.text,
            "tool_rounds": result.tool_rounds,
            "target_peer": {
                "peer_type": target_peer.peer_type.value,
                "peer_id": target_peer.peer_id,
                "username": target_peer.username,
                "title": target_peer.title,
            },
        }

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        raw = str(value).strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
        return None

    @staticmethod
    def _dict_to_peer(raw: dict[str, Any]) -> PeerRef:
        return PeerRef(
            peer_type=PeerType(str(raw["peer_type"])),
            peer_id=int(raw["peer_id"]),
            access_hash=raw.get("access_hash"),
            username=raw.get("username"),
            title=raw.get("title"),
        )
