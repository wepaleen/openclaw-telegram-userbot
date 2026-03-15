"""Tool-calling runtime that converts inbound Telegram events into OpenClaw loops."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from apps.openclaw_adapter.client import OpenClawChatClient
from apps.openclaw_adapter.instructions import DEFAULT_SYSTEM_INSTRUCTIONS
from apps.openclaw_adapter.local_commands import try_parse_local
from apps.openclaw_adapter.tools import build_default_tool_schemas
from apps.security import (
    Role,
    SecurityViolation,
    check_input_safety,
    filter_result_for_role,
    filter_tool_schemas,
    get_user_role,
    is_tool_allowed,
    sanitize_tool_args,
)
from config import settings
from shared.schemas.telegram import InboundTelegramEvent, PeerRef

log = logging.getLogger("openclaw_adapter.runtime")

ToolExecutor = Callable[[str, dict[str, Any], InboundTelegramEvent], Awaitable[dict[str, Any]]]

_ACTION_KEYWORDS = re.compile(
    r"напиши|отправь|пошли|скинь|перешли|закрепи|найди|покажи|прочитай|"
    r"напомни|поставь|создай|удали|отмени|запланируй|"
    r"задач[аеуи]|напоминани[еяй]|дедлайн|таск|"
    r"send|write|forward|pin|search|read|list|get|show|remind|schedule|task|"
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
    """Dual-LLM agent runtime: OpenClaw for chat, OpenRouter for tool calling."""

    def __init__(
        self,
        *,
        client: OpenClawChatClient | None = None,
        system_instructions: str = DEFAULT_SYSTEM_INSTRUCTIONS,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        # Tool-calling client (OpenRouter/DeepSeek — paid, supports tools)
        self.client = client or OpenClawChatClient()
        # Conversational client (OpenClaw/Codex — free, no tool support)
        self.chat_client: OpenClawChatClient | None = None
        if settings.openclaw_url:
            openclaw_completions_url = settings.openclaw_url.replace(
                "/v1/responses", "/v1/chat/completions"
            )
            self.chat_client = OpenClawChatClient(
                base_url=openclaw_completions_url,
                token=settings.openclaw_token,
                model=settings.openclaw_model,
            )
            log.info(
                "Dual-LLM mode: chat=%s, tools=%s",
                settings.openclaw_model,
                settings.llm_model,
            )
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
        # ── Security: role & input checks ──
        user_role = get_user_role(event.sender_id)
        if user_role == Role.BLOCKED:
            log.warning("Blocked user %s attempted access", event.sender_id)
            return AgentRunResult(
                text="Доступ запрещён.",
                messages=[], raw_response={}, tool_rounds=0,
            )

        safety_warning = check_input_safety(event.text)
        if safety_warning:
            log.warning("Input blocked for user %s: %s", event.sender_id, event.text[:100])
            return AgentRunResult(
                text=safety_warning,
                messages=[], raw_response={}, tool_rounds=0,
            )

        # ── Level 1: Local regex parser (0 tokens, free) ──
        local_cmd = try_parse_local(event.text)
        if local_cmd is not None:
            if not is_tool_allowed(local_cmd.tool_name, user_role):
                return AgentRunResult(
                    text=f"У вас нет доступа к команде {local_cmd.tool_name}.",
                    messages=[], raw_response={}, tool_rounds=0,
                )
            log.info("Level-1 local command: %s", local_cmd.tool_name)
            try:
                safe_args = sanitize_tool_args(local_cmd.tool_name, local_cmd.tool_args)
            except SecurityViolation as e:
                return AgentRunResult(
                    text=str(e), messages=[], raw_response={}, tool_rounds=0,
                )
            result = await execute_tool(local_cmd.tool_name, safe_args, event)
            if not result.get("error"):
                filtered = filter_result_for_role(result, user_role)
                text = self._format_local_result(local_cmd.tool_name, filtered)
                return AgentRunResult(
                    text=text,
                    messages=[],
                    raw_response={},
                    tool_rounds=0,
                )
            # If local execution failed, fall through to LLM
            log.info("Level-1 failed (%s), falling through to LLM", result.get("error"))

        # ── Level 2/3: LLM-based processing ──
        max_rounds = max_tool_rounds or settings.max_tool_calls
        user_content = self._build_user_content(event, recent_context, available_chats)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_instructions},
            {"role": "user", "content": user_content},
        ]

        # Filter tools based on user role
        role_tools = filter_tool_schemas(self.tools, user_role)
        needs_tools = self._looks_like_action(event.text)

        if needs_tools:
            # Action request → use paid tool-calling LLM (OpenRouter/DeepSeek)
            log.info("Routing to tool LLM (action detected, role=%s, tools=%d)", user_role.value, len(role_tools))
            first_tool_choice = "required"
            response = await self.client.complete(
                messages=messages,
                tools=role_tools,
                session_key=event.session_key,
                tool_choice=first_tool_choice,
            )
        elif self.chat_client:
            # Conversational request → use free OpenClaw (no tools)
            log.info("Routing to OpenClaw (conversation)")
            response = await self.chat_client.complete(
                messages=messages,
                session_key=event.session_key,
            )
            # If OpenClaw returns a clean text response, return it directly
            calls = self.client.extract_tool_calls(response)
            if not calls:
                return AgentRunResult(
                    text=self.client.extract_text(response),
                    messages=messages,
                    raw_response=response,
                    tool_rounds=0,
                )
            # Unlikely but if OpenClaw somehow returns tool calls, proceed with tool loop
        else:
            # No OpenClaw configured → use tool LLM for everything
            first_tool_choice = "auto"
            response = await self.client.complete(
                messages=messages,
                tools=role_tools,
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
                log.info(
                    "Tool call: %s(id=%s) args=%s",
                    call.name,
                    call.call_id,
                    json.dumps(call.arguments, ensure_ascii=False)[:500],
                )

                # Security: check tool permission and sanitize args
                if not is_tool_allowed(call.name, user_role):
                    log.warning("Tool %s blocked for role %s", call.name, user_role.value)
                    result = {"error": f"Нет доступа к {call.name}"}
                    messages.append({"role": "tool", "tool_call_id": call.call_id, "content": json.dumps(result, ensure_ascii=False)})
                    continue
                try:
                    call.arguments = sanitize_tool_args(call.name, call.arguments)
                except SecurityViolation as e:
                    result = {"error": str(e)}
                    messages.append({"role": "tool", "tool_call_id": call.call_id, "content": json.dumps(result, ensure_ascii=False)})
                    continue
                result = await execute_tool(call.name, call.arguments, event)
                result_str = json.dumps(result, ensure_ascii=False)
                log.info(
                    "Tool result: %s(id=%s) -> %s",
                    call.name,
                    call.call_id,
                    result_str[:500],
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": result_str,
                    }
                )

            response = await self.client.complete(
                messages=messages,
                tools=role_tools,
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
    def _format_local_result(tool_name: str, result: dict[str, Any]) -> str:
        """Format tool result into human-readable text without using LLM."""
        if tool_name == "set_reminder":
            local_time = result.get("remind_at_local") or result.get("fire_at", "")
            return f"Напоминание установлено на {local_time}."

        if tool_name == "send_message":
            peer = result.get("target_peer", {})
            label = peer.get("title") or peer.get("username") or "чат"
            return f"Сообщение отправлено в {label}."

        if tool_name == "send_private_message":
            peer = result.get("target_peer", {})
            label = peer.get("title") or peer.get("username") or "пользователь"
            return f"Сообщение отправлено {label}."

        if tool_name == "list_tasks":
            tasks = result.get("tasks", [])
            if not tasks:
                return "Задач нет."
            lines = []
            for t in tasks:
                status = t.get("status", "open")
                due = t.get("due_at", "")
                line = f"• [{status}] {t.get('title', '—')}"
                if due:
                    line += f" (до {due})"
                lines.append(line)
            return "Задачи:\n" + "\n".join(lines)

        if tool_name == "list_reminders":
            by_status = result.get("reminders_by_status", {})
            pending = by_status.get("pending", [])
            if not pending and not by_status:
                reminders = result.get("reminders", [])
                if not reminders:
                    return "Активных напоминаний нет."
                pending = reminders
            if not pending:
                return "Активных напоминаний нет."
            lines = []
            for r in pending:
                local_time = r.get("fire_at_local") or r.get("fire_at", "")
                lines.append(f"• #{r.get('id', '?')} [{local_time}] {r.get('text', '—')}")
            return "Напоминания:\n" + "\n".join(lines)

        if tool_name == "cancel_reminder":
            return f"Напоминание #{result.get('reminder_id', '?')} отменено."

        return json.dumps(result, ensure_ascii=False, indent=2)

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
