"""Tool-calling runtime that converts inbound Telegram events into OpenClaw loops."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable

from apps.openclaw_adapter.client import OpenClawChatClient
from apps.task_core.store.session_cache import load_session, save_session
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
    r"send\s|write\s|forward|pin\s|search\s|remind|schedule|"
    r"посмотри|проверь|узнай|спроси",
    re.IGNORECASE,
)

# Emergency escalation patterns — always route to tool-calling LLM
_ESCALATION_KEYWORDS = re.compile(
    r"расторга[юет]|расторжени[еяю]|разрыва[юет]\s+договор|"
    r"уходи[мт]|ухожу|покида[юет]|прекраща[юет]|"
    r"сайт\s+(?:упал|не\s+работает|лежит|недоступен)|"
    r"оплат[аеуы]\s+(?:не\s+прошл|проблем|задерж)|"
    r"суд\s|иск\s|претензи[яию]|юрист|адвокат|"
    r"репутаци[яию]|скандал|негатив\s+в\s+(?:сми|соцсет|интернет)|"
    r"отказыва[юет](?:сь|мся)|отказ\s+от\s+(?:услуг|сотрудничеств)",
    re.IGNORECASE,
)

_CAPABILITY_QUESTION_CUES = re.compile(
    r"имеешь\s+доступ|есть\s+ли\s+доступ|есть\s+доступ|"
    r"умеешь|поддерживаешь|работаешь\s+с|можно\s+ли\s+подключить|"
    r"можешь\s+ли|ты\s+(?:можешь|умеешь)|как\s+(?:можно|ты\s+можешь)|"
    r"расскажи|объясни|что\s+(?:такое|значит|делает)|как\s+работает",
    re.IGNORECASE,
)

_ACTION_INTENT_VERBS = re.compile(
    r"напиши|отправь|пошли|скинь|перешли|закрепи|найди|покажи|прочитай|"
    r"напомни|поставь|создай|удали|отмени|запланируй|посмотри|проверь|узнай|спроси",
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

        system_prompt = self.system_instructions
        is_emergency = self._is_emergency(event.text)
        if is_emergency:
            system_prompt += (
                "\n\n## ⚠️ ОБНАРУЖЕНА ЭКСТРЕННАЯ СИТУАЦИЯ\n"
                "В сообщении пользователя обнаружены признаки экстренного случая. "
                "Ты ОБЯЗАН НЕМЕДЛЕННО:\n"
                "1. Вызвать send_message с текстом, содержащим @dlnadezhda и @anylise и краткое описание ситуации\n"
                "2. Только ПОСЛЕ этого — ответить пользователю\n"
                "НЕ просто говори «передам коллегам». ВЫЗОВИ send_message tool прямо сейчас."
            )
            log.warning("Emergency escalation detected in message from %s: %s", event.sender_id, event.text[:100])

        # Filter tools based on user role
        role_tools = filter_tool_schemas(self.tools, user_role)
        needs_tools = self._needs_tools(event.text) or is_emergency

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Inject cached session history (prior assistant/tool rounds)
        try:
            cached = await load_session(event.session_key)
            if cached:
                # Only keep non-system messages from cache
                history = [m for m in cached if m.get("role") != "system"]
                if not needs_tools and self.chat_client:
                    # For conversational OpenClaw (no tools), strip tool messages
                    # to avoid confusing the model with tool_calls/tool results
                    history = self._filter_history_for_chat(history)
                messages.extend(history)
                log.info("Loaded %d cached messages for session %s", len(history), event.session_key)
        except Exception as e:
            log.warning("Failed to load session cache: %s", e)

        messages.append({"role": "user", "content": user_content})

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
                extracted = self.client.extract_text(response)
                if extracted:
                    await self._save_session(event.session_key, messages, response)
                    return AgentRunResult(
                        text=extracted,
                        messages=messages,
                        raw_response=response,
                        tool_rounds=0,
                    )
                # Empty text (pseudo-tool-call stripped) — retry with tool-calling LLM
                log.info("OpenClaw returned empty text (pseudo-tool-call stripped), retrying with tool LLM")
                response = await self.client.complete(
                    messages=messages,
                    tools=role_tools,
                    session_key=event.session_key,
                    tool_choice="auto",
                )
            # If OpenClaw somehow returns tool calls, also proceed with tool LLM
        else:
            # No OpenClaw configured → use tool LLM for everything
            first_tool_choice = "auto"
            response = await self.client.complete(
                messages=messages,
                tools=role_tools,
                session_key=event.session_key,
                tool_choice=first_tool_choice,
            )

        escalation_sent = False  # Track if escalation message was sent during tool loop

        for tool_round in range(max_rounds):
            calls = self.client.extract_tool_calls(response)
            if not calls:
                llm_text = self.client.extract_text(response)
                # If text was stripped (pseudo-tool-call), provide a safe fallback
                if not llm_text:
                    llm_text = "Не смог обработать запрос. Попробуйте переформулировать."

                # Fallback: if emergency detected but no escalation was sent,
                # force escalation by calling send_message directly
                if is_emergency and not escalation_sent:
                    log.warning(
                        "Emergency fallback: LLM finished without escalation, forcing send_message"
                    )
                    escalation_text = (
                        f"⚠️ @dlnadezhda @anylise, добрый день! Прошу обратить внимание — "
                        f"поступило важное сообщение от клиента: «{event.text[:500]}». "
                        f"Требуется оперативное участие."
                    )
                    escalation_result = await execute_tool(
                        "send_message",
                        {"text": escalation_text},
                        event,
                    )
                    log.info("Emergency fallback send_message result: %s", escalation_result)

                await self._save_session(event.session_key, messages, response)
                return AgentRunResult(
                    text=llm_text,
                    messages=messages,
                    raw_response=response,
                    tool_rounds=tool_round,
                )

            assistant_message = response.get("choices", [{}])[0].get("message", {})
            messages.append(assistant_message)

            # Guard: if LLM calls send_message to the SAME chat with no
            # chat_query/target_query, treat the text as a normal reply instead
            # of executing the tool (prevents raw JSON from being shown).
            if len(calls) == 1 and calls[0].name == "send_message":
                sm_args = calls[0].arguments
                if (
                    not sm_args.get("chat_query")
                    and not sm_args.get("target_query")
                    and not sm_args.get("topic_query")
                    and sm_args.get("text")
                ):
                    log.info(
                        "Intercepted send_message to current chat — returning text as reply"
                    )
                    await self._save_session(event.session_key, messages, response)
                    return AgentRunResult(
                        text=str(sm_args["text"]),
                        messages=messages,
                        raw_response=response,
                        tool_rounds=tool_round,
                    )

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
                # Track if escalation was sent via send_message with @mentions
                if is_emergency and call.name == "send_message":
                    msg_text = str(call.arguments.get("text", ""))
                    if "@dlnadezhda" in msg_text or "@anylise" in msg_text:
                        escalation_sent = True
                        log.info("Escalation tag detected in send_message call")
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

        await self._save_session(event.session_key, messages, response)
        return AgentRunResult(
            text="Остановлено: слишком много циклов tools.",
            messages=messages,
            raw_response=response,
            tool_rounds=max_rounds,
        )

    async def stream(
        self,
        *,
        event: InboundTelegramEvent,
        recent_context: list[dict[str, Any]],
        available_chats: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream conversational response (no tools). Yields text deltas."""
        if not self.chat_client:
            raise RuntimeError("stream requires a chat_client (OpenClaw)")

        user_content = self._build_user_content(event, recent_context, available_chats)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_instructions},
        ]

        # Load cached session history
        try:
            cached = await load_session(event.session_key)
            if cached:
                history = self._filter_history_for_chat(
                    [m for m in cached if m.get("role") != "system"]
                )
                messages.extend(history)
        except Exception as e:
            log.warning("Failed to load session cache for stream: %s", e)

        messages.append({"role": "user", "content": user_content})

        full_text = ""
        async for chunk in self.chat_client.stream_complete(
            messages=messages,
            session_key=event.session_key,
        ):
            full_text += chunk
            yield chunk

        # Save session with final assistant message
        if full_text.strip():
            all_messages = list(messages)
            all_messages.append({"role": "assistant", "content": full_text})
            try:
                await save_session(event.session_key, all_messages)
            except Exception as e:
                log.warning("Failed to save session cache after stream: %s", e)

    @staticmethod
    async def _save_session(
        session_key: str,
        messages: list[dict[str, Any]],
        response: dict[str, Any],
    ) -> None:
        """Save conversation state to persistent cache."""
        try:
            # Append final assistant message if present
            final_msg = response.get("choices", [{}])[0].get("message")
            all_messages = list(messages)
            if final_msg:
                all_messages.append(final_msg)
            await save_session(session_key, all_messages)
        except Exception as e:
            log.warning("Failed to save session cache for %s: %s", session_key, e)

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
    def _filter_history_for_chat(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip tool-call and tool-result messages for non-tool-calling LLMs.

        Keeps only user messages and assistant messages that have plain text content
        (no tool_calls). This prevents confusing conversational models with
        tool-related message structures they can't interpret.
        """
        filtered: list[dict[str, Any]] = []
        for msg in history:
            role = msg.get("role")
            if role == "tool":
                continue
            if role == "assistant":
                # Skip assistant messages that are purely tool calls (no text)
                if msg.get("tool_calls") and not (msg.get("content") or "").strip():
                    continue
                # For assistant messages with both content and tool_calls, keep only content
                if msg.get("tool_calls"):
                    filtered.append({"role": "assistant", "content": msg["content"]})
                    continue
            filtered.append(msg)
        return filtered

    @staticmethod
    def _looks_like_action(text: str) -> bool:
        return bool(_ACTION_KEYWORDS.search(text) or _ESCALATION_KEYWORDS.search(text))

    @staticmethod
    def _is_emergency(text: str) -> bool:
        return bool(_ESCALATION_KEYWORDS.search(text))

    @staticmethod
    def _is_capability_question(text: str) -> bool:
        if not _CAPABILITY_QUESTION_CUES.search(text):
            return False
        return not bool(_ACTION_INTENT_VERBS.search(text))

    @classmethod
    def _needs_tools(cls, text: str) -> bool:
        return cls._looks_like_action(text) and not cls._is_capability_question(text)

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
