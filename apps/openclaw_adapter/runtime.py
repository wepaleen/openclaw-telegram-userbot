"""Tool-calling runtime that converts inbound Telegram events into OpenClaw loops."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from apps.openclaw_adapter.client import OpenClawChatClient
from apps.openclaw_adapter.instructions import DEFAULT_SYSTEM_INSTRUCTIONS
from apps.openclaw_adapter.tools import build_default_tool_schemas
from config import settings
from shared.schemas.telegram import InboundTelegramEvent, PeerRef

log = logging.getLogger("openclaw_adapter.runtime")

ToolExecutor = Callable[[str, dict[str, Any], InboundTelegramEvent], Awaitable[dict[str, Any]]]

_ACTION_KEYWORDS = re.compile(
    r"напиши|отправь|пошли|скинь|перешли|закрепи|найди|покажи|прочитай|"
    r"send|write|forward|pin|search|read|list|get|show|"
    r"посмотри|проверь|узнай|спроси|скажи\s",
    re.IGNORECASE,
)


@dataclass(slots=True)
class AgentRunResult:
    """Final result of one OpenClaw tool loop."""

    text: str
    messages: list[dict[str, Any]]
    raw_response: dict[str, Any]
    tool_rounds: int = 0


def _serialize_peer(peer: PeerRef) -> dict[str, Any]:
    return {
        "peer_type": peer.peer_type.value,
        "peer_id": peer.peer_id,
        "access_hash": peer.access_hash,
        "username": peer.username,
        "title": peer.title,
    }


class OpenClawAgentRuntime:
    """OpenClaw-backed agent runtime that relies on external typed tools."""

    def __init__(
        self,
        *,
        client: OpenClawChatClient | None = None,
        system_instructions: str = DEFAULT_SYSTEM_INSTRUCTIONS,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        self.client = client or OpenClawChatClient()
        self.system_instructions = system_instructions
        self.tools = tools or build_default_tool_schemas()

    async def run(
        self,
        *,
        event: InboundTelegramEvent,
        recent_context: list[dict[str, Any]],
        available_chats: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_tool_rounds: int | None = None,
    ) -> AgentRunResult:
        max_rounds = max_tool_rounds or settings.max_tool_calls
        user_content = self._build_user_content(event, recent_context, available_chats)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_instructions},
            {"role": "user", "content": user_content},
        ]

        first_tool_choice = "required" if self._looks_like_action(event.text) else "auto"
        response = await self.client.complete(
            messages=messages,
            tools=self.tools,
            session_key=event.session_key,
            tool_choice=first_tool_choice,
        )

        for tool_round in range(max_rounds):
            calls = self.client.extract_tool_calls(response)
            if not calls:
                return AgentRunResult(
                    text=self.client.extract_text(response),
                    messages=messages,
                    raw_response=response,
                    tool_rounds=tool_round,
                )

            assistant_message = response.get("choices", [{}])[0].get("message", {})
            messages.append(assistant_message)

            for call in calls:
                result = await execute_tool(call.name, call.arguments, event)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            response = await self.client.complete(
                messages=messages,
                tools=self.tools,
                session_key=event.session_key,
                tool_choice="auto",
            )

        return AgentRunResult(
            text="Остановлено: слишком много циклов tools.",
            messages=messages,
            raw_response=response,
            tool_rounds=max_rounds,
        )

    @staticmethod
    def _looks_like_action(text: str) -> bool:
        return bool(_ACTION_KEYWORDS.search(text))

    def _build_user_content(
        self,
        event: InboundTelegramEvent,
        recent_context: list[dict[str, Any]],
        available_chats: list[dict[str, Any]],
    ) -> str:
        now_local = datetime.now(settings.tzinfo)
        now_utc = datetime.now(timezone.utc)
        context = {
            "now": {
                "local": now_local.isoformat(),
                "utc": now_utc.isoformat(),
                "timezone": settings.bot_timezone,
            },
            "event": {
                "event_id": event.event_id,
                "account_id": event.account_id,
                "peer": _serialize_peer(event.peer),
                "sender_id": event.sender_id,
                "sender_username": event.sender_username,
                "message_id": event.message_id,
                "text": event.text,
                "date_utc": event.date_utc.isoformat(),
                "reply_to_msg_id": event.reply_to_msg_id,
                "top_msg_id": event.top_msg_id,
                "is_topic_message": event.is_topic_message,
                "session_key": event.session_key,
            },
            "recent_context": recent_context,
            "available_chats": available_chats,
        }
        return (
            "Контекст Telegram:\n"
            + json.dumps(context, ensure_ascii=False, indent=2)
            + "\n\nТекущая дата и время: "
            + now_local.strftime("%Y-%m-%d %H:%M:%S %Z")
            + "\n\nЗапрос пользователя: "
            + event.text
        )
