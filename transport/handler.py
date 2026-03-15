"""Telegram message handler — the main pipeline."""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import settings
from transport.telegram_api import TelegramAPI, serialize_message
from router.intent import classify_intent
from resolver.entities import ResolutionError, resolve_action_params
from resolver.contacts import get_contacts_summary, add_contact, list_contacts
from resolver.chats import get_chats_summary, sync_chats, sync_topics
from executor.actions import Action, ActionType, RiskLevel
from executor.executor import ActionExecutor
from policy.checker import (
    needs_confirmation,
    confirm_session_writes,
    format_confirmation,
    reset_session_writes,
)
from audit.logger import log_action, Timer
from planner.llm_client import call_llm
from scheduler.task_store import (
    create_task, list_tasks, update_task, complete_task,
    create_reminder, list_reminders, cancel_reminder,
    format_local_datetime, parse_datetime_input,
)

log = logging.getLogger("handler")

chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_pending_confirmations: dict[str, Action] = {}


def _topic_key(message: Message) -> int:
    return getattr(message, "reply_to_top_message_id", None) or 0


def _session_key(message: Message) -> str:
    return f"tg:{message.chat.id}:thread:{_topic_key(message)}"


def normalize_input(message: Message) -> str | None:
    text = (message.text or "").strip()
    if not text:
        return None
    if message.chat.type == "private":
        return text
    is_reply_to_me = bool(
        message.reply_to_message and message.reply_to_message.outgoing
    )
    if text.startswith(settings.group_trigger):
        stripped = text[len(settings.group_trigger):].strip()
        return stripped or None
    if is_reply_to_me:
        return text
    return None


async def send_chunks(app: Client, message: Message, text: str) -> None:
    parts = [text[i:i + 3500] for i in range(0, len(text), 3500)] or ["(пустой ответ)"]
    topic_reply = _topic_key(message) or None
    first = True
    for part in parts:
        if first:
            await message.reply_text(part)
            first = False
        else:
            kwargs: dict[str, Any] = {}
            if topic_reply:
                kwargs["reply_to_message_id"] = topic_reply
            await app.send_message(message.chat.id, part, **kwargs)


def _format_result(action: Action, result: dict[str, Any]) -> str:
    """Format executor result as user-facing text."""
    if result.get("error"):
        return f"Ошибка: {result['error']}"

    t = action.type

    if t == ActionType.SEND_PRIVATE:
        return f"Отправил в личку {result.get('target', '?')}."
    if t == ActionType.SEND_CHAT:
        return f"Отправил в чат {result.get('chat_id', '?')}."
    if t == ActionType.SEND_TOPIC:
        mention = result.get("mention_username")
        extra = f" с пингом {mention}" if mention else ""
        return f"Отправил в тему {result.get('topic_id', '?')}{extra}."
    if t == ActionType.SEND_LINK:
        return f"Отправил по ссылке."
    if t == ActionType.FORWARD:
        return f"Переслал сообщение."
    if t == ActionType.PIN:
        return f"Закрепил сообщение."
    if t == ActionType.USER_INFO:
        u = result
        parts = [f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()]
        if u.get("username"):
            parts.append(u["username"])
        return " | ".join(parts)

    if t == ActionType.LIST_CHATS:
        chats = result.get("chats", [])
        if not chats:
            return "Нет доступных чатов."
        lines = [f"Доступные чаты ({len(chats)}):"]
        for c in chats:
            line = f"• {c.get('title', '?')} ({c.get('chat_id', '?')})"
            if c.get("is_forum"):
                line += " [форум]"
            lines.append(line)
        return "\n".join(lines)

    if t == ActionType.LIST_TOPICS:
        topics = result.get("topics", [])
        if not topics:
            return "Нет тем."
        lines = ["Темы:"]
        for tp in topics:
            lines.append(f"• {tp.get('title', '?')} (id: {tp.get('topic_id', '?')})")
        return "\n".join(lines)

    if t == ActionType.SEARCH:
        msgs = result.get("messages", [])
        if not msgs:
            return "Ничего не найдено."
        lines = [f"Найдено {len(msgs)} сообщений:"]
        for m in msgs[:10]:
            sender = (m.get("sender") or {}).get("name", "?")
            chat_prefix = ""
            if m.get("chat_title"):
                chat_prefix = f"{m.get('chat_title')}: "
            lines.append(
                f"• [{m.get('date', '?')}] {chat_prefix}{sender}: {m.get('text', '')[:100]}"
            )
        return "\n".join(lines)

    if t in (ActionType.GET_CHAT_CONTEXT, ActionType.GET_TOPIC_CONTEXT):
        msgs = result.get("messages", [])
        if not msgs:
            return "Нет сообщений."
        lines = []
        for m in msgs[-15:]:
            sender = (m.get("sender") or {}).get("name", "?")
            lines.append(f"[{m.get('date', '?')}] {sender}: {m.get('text', '')[:200]}")
        return "\n".join(lines)

    if t == ActionType.SUMMARIZE:
        return result.get("text", "Не удалось получить резюме.")

    if t == ActionType.RESPOND_TEXT:
        return result.get("text", "")

    if t == ActionType.LIST_MEMBERS:
        members = result.get("members", [])
        if not members:
            return "Нет участников."
        lines = [f"Участники ({len(members)}):"]
        for m in members:
            line = f"• {m.get('name', '?')}"
            if m.get("username"):
                line += f" ({m['username']})"
            lines.append(line)
        return "\n".join(lines)

    return json.dumps(result, ensure_ascii=False, indent=2)[:2000]


def _format_tasks(tasks: list[dict]) -> str:
    if not tasks:
        return "Нет задач."
    lines = ["Задачи:"]
    for t in tasks:
        status = {"open": "⬜", "in_progress": "🔄", "done": "✅", "cancelled": "❌"}.get(
            t.get("status", ""), "?"
        )
        line = f"{status} #{t['id']} {t['title']}"
        if t.get("assignee"):
            line += f" → {t['assignee']}"
        if t.get("due_at"):
            line += f" (до {format_local_datetime(t['due_at']) or t['due_at']})"
        lines.append(line)
    return "\n".join(lines)


def _format_reminders(reminders: list[dict]) -> str:
    if not reminders:
        return "Нет напоминаний."
    lines = ["Напоминания:"]
    for r in reminders:
        line = f"• #{r['id']} [{format_local_datetime(r['fire_at']) or r['fire_at']}] {r['text'][:80]}"
        lines.append(line)
    return "\n".join(lines)


async def _handle_task_action(action: Action, message: Message) -> str:
    """Handle task/reminder actions that don't go through executor."""
    t = action.type
    p = action.params

    if t == ActionType.CREATE_TASK:
        due_at = parse_datetime_input(
            p.get("due") or p.get("due_at") or p.get("deadline")
        )
        result = await create_task(
            title=p.get("title", ""),
            description=p.get("description"),
            assignee=p.get("assignee"),
            due_at=due_at,
            source_chat_id=message.chat.id,
            source_message_id=message.id,
        )
        reply = f"Задача #{result['task_id']} создана: {result['title']}"
        if due_at:
            reply += f" (до {format_local_datetime(due_at) or due_at})"
        return reply

    if t == ActionType.LIST_TASKS:
        tasks = await list_tasks(
            status=p.get("status"),
            assignee=p.get("assignee"),
        )
        return _format_tasks(tasks)

    if t == ActionType.UPDATE_TASK:
        task_id = p.get("task_id")
        if not task_id:
            return "Укажи номер задачи."
        result = await update_task(int(task_id), **{
            k: v for k, v in p.items() if k != "task_id"
        })
        return f"Задача #{task_id} обновлена." if result.get("ok") else str(result)

    if t == ActionType.CREATE_REMINDER:
        text = p.get("text", "")
        fire_at = parse_datetime_input(
            p.get("when") or p.get("fire_at") or p.get("time") or p.get("delta")
        )

        if not fire_at:
            # Default: 1 hour from now
            fire_at = parse_datetime_input("1h")

        result = await create_reminder(
            text=text,
            fire_at=fire_at,
            target_chat_id=message.chat.id,
            target_topic_id=_topic_key(message) or None,
        )
        return (
            f"Напоминание #{result['reminder_id']} установлено на "
            f"{format_local_datetime(result['fire_at']) or result['fire_at']}"
        )

    if t == ActionType.LIST_REMINDERS:
        reminders = await list_reminders(status=p.get("status", "pending"))
        return _format_reminders(reminders)

    if t == ActionType.CANCEL_REMINDER:
        rid = p.get("reminder_id")
        if not rid:
            return "Укажи номер напоминания."
        await cancel_reminder(int(rid))
        return f"Напоминание #{rid} отменено."

    if t == ActionType.ADD_CONTACT:
        name = p.get("name", "")
        target = p.get("target", "")
        user_id = int(target) if str(target).lstrip("-").isdigit() else None
        username = target if str(target).startswith("@") else None
        result = await add_contact(
            display_name=name, username=username, user_id=user_id,
        )
        return f"Контакт «{name}» добавлен."

    if t == ActionType.LIST_CONTACTS:
        contacts = await list_contacts()
        if not contacts:
            return "Контактная книга пуста."
        lines = ["Контакты:"]
        for c in contacts:
            line = f"• {c['display_name']}"
            if c.get("username"):
                line += f" ({c['username']})"
            lines.append(line)
        return "\n".join(lines)

    return ""


async def process_message(
    app_client: Client,
    tg_api: TelegramAPI,
    executor: ActionExecutor,
    message: Message,
    text: str,
) -> None:
    """Main pipeline: classify → resolve → policy → execute → respond."""
    session_key = _session_key(message)

    # Check for pending confirmation
    if session_key in _pending_confirmations:
        response = text.strip().lower()
        if response in ("да", "yes", "y", "ок", "ok", "давай"):
            action = _pending_confirmations.pop(session_key)
            confirm_session_writes(session_key)
            with Timer() as timer:
                result = await executor.execute(action, session_key)
            reply = _format_result(action, result)
            await log_action(
                action_type=action.type.value,
                source_chat_id=message.chat.id,
                source_user_id=getattr(message.from_user, "id", None),
                params=action.params, result=result,
                success=not result.get("error"),
                latency_ms=timer.elapsed_ms,
            )
            await send_chunks(app_client, message, reply)
            return
        else:
            _pending_confirmations.pop(session_key)
            await message.reply_text("Отменено.")
            return

    with Timer() as timer:
        # 1. Classify intent
        contacts_json = await get_contacts_summary()
        chats_json = await get_chats_summary()
        action = await classify_intent(
            text, contacts_json, chats_json, session_key,
        )
        llm_used = action.source == "llm"

        # 2. Handle task/reminder/contact actions locally
        if action.type in {
            ActionType.CREATE_TASK, ActionType.LIST_TASKS, ActionType.UPDATE_TASK,
            ActionType.CREATE_REMINDER, ActionType.LIST_REMINDERS, ActionType.CANCEL_REMINDER,
            ActionType.ADD_CONTACT, ActionType.LIST_CONTACTS,
        }:
            reply = await _handle_task_action(action, message)
            await log_action(
                action_type=action.type.value, intent=action.type.value,
                source_chat_id=message.chat.id,
                source_user_id=getattr(message.from_user, "id", None),
                params=action.params, success=True,
                llm_used=llm_used, latency_ms=timer.elapsed_ms,
            )
            await send_chunks(app_client, message, reply)
            return

        # 3. Handle respond_text (general conversation / LLM response)
        if action.type == ActionType.RESPOND_TEXT:
            if action.params.get("needs_llm_response"):
                # Ask LLM for a conversational response
                response = await call_llm(
                    system="Ты — рабочий ассистент. Отвечай по-русски, коротко и по делу.",
                    user=text,
                    session_key=session_key,
                )
                reply = response or "Не смог обработать запрос."
            else:
                reply = action.params.get("text", "Не понял запрос.")
            await send_chunks(app_client, message, reply)
            return

        # 4. Resolve entities in params
        try:
            action.params = await resolve_action_params(
                action.type.value,
                action.params,
                fallback_chat_id=message.chat.id,
            )
        except ResolutionError as e:
            await send_chunks(app_client, message, f"Не смог однозначно определить адресата: {e}")
            return

        # 5. Check policy
        if needs_confirmation(action, session_key):
            _pending_confirmations[session_key] = action
            confirm_text = format_confirmation(action)
            await message.reply_text(f"{confirm_text}\n(да/нет)")
            return

        # 6. Execute
        result = await executor.execute(action, session_key)

    # 7. Log & respond
    await log_action(
        action_type=action.type.value, intent=action.type.value,
        source_chat_id=message.chat.id,
        source_message_id=message.id,
        source_user_id=getattr(message.from_user, "id", None),
        target_chat_id=action.params.get("chat_id"),
        target_topic_id=action.params.get("topic_id"),
        params=action.params, result=result,
        success=not result.get("error"),
        error=result.get("error"),
        llm_used=llm_used,
        latency_ms=timer.elapsed_ms,
    )
    reply = _format_result(action, result)
    await send_chunks(app_client, message, reply)


def register_handlers(app_client: Client, tg_api: TelegramAPI) -> None:
    """Register Pyrogram message handlers."""
    executor = ActionExecutor(tg_api)

    @app_client.on_message(filters.text)
    async def handle_text(_: Client, message: Message):
        if message.outgoing:
            return
        if not settings.is_allowed_chat(message.chat.id):
            is_private = str(getattr(message.chat, "type", "")) in {"private", "ChatType.PRIVATE"}
            if not is_private:
                return

        text = normalize_input(message)
        if not text:
            return

        # Bot commands
        if text.strip() == "!reset":
            _pending_confirmations.pop(_session_key(message), None)
            reset_session_writes(_session_key(message))
            await message.reply_text("Сессия сброшена.")
            return

        if text.strip() == "!sync":
            await sync_chats(tg_api)
            # Sync topics for forum chats
            from db import get_db
            db = await get_db()
            forums = await db.execute_fetchall(
                "SELECT chat_id FROM chat_index WHERE is_forum = 1"
            )
            for f in forums:
                try:
                    await sync_topics(tg_api, f["chat_id"])
                except Exception:
                    pass
            await message.reply_text("Чаты и темы синхронизированы.")
            return

        if text.strip() == "!chats":
            chats = await tg_api.list_available_chats(limit=30)
            lines = [f"Чаты ({len(chats)}):"]
            for c in chats:
                line = f"• {c.get('title', '?')} ({c.get('chat_id', '?')})"
                if c.get("is_forum"):
                    line += " [форум]"
                lines.append(line)
            await send_chunks(app_client, message, "\n".join(lines))
            return

        lock = chat_locks[_session_key(message)]
        async with lock:
            try:
                await process_message(app_client, tg_api, executor, message, text)
            except Exception as e:
                log.error("Pipeline error: %s", e, exc_info=True)
                await message.reply_text(f"Ошибка: {type(e).__name__}: {e}")
