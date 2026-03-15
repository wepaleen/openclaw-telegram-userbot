"""ActionExecutor: executes resolved actions via Telegram API."""

import json
import logging
from typing import Any

from executor.actions import Action, ActionType
from transport.telegram_api import TelegramAPI
from planner.llm_client import call_llm
from planner.prompts import SUMMARIZE_MESSAGES

log = logging.getLogger("executor")


class ActionExecutor:
    def __init__(self, tg_api: TelegramAPI):
        self.tg = tg_api

    async def execute(self, action: Action, session_key: str = "") -> dict[str, Any]:
        """Execute an action and return the result."""
        t = action.type
        p = action.params

        try:
            if t == ActionType.SEND_PRIVATE:
                return await self.tg.send_private_message(
                    target=str(p["target"]), text=str(p["text"]),
                )

            if t == ActionType.SEND_CHAT:
                return await self.tg.send_to_chat(
                    chat_id=int(p["chat_id"]), text=str(p["text"]),
                    reply_to_message_id=p.get("reply_to_message_id"),
                )

            if t == ActionType.SEND_TOPIC:
                return await self.tg.send_to_topic(
                    chat_id=int(p["chat_id"]),
                    topic_id=int(p["topic_id"]),
                    text=str(p["text"]),
                    mention_username=p.get("mention_username"),
                )

            if t == ActionType.SEND_LINK:
                return await self.tg.send_to_link(
                    link=str(p["link"]), text=str(p["text"]),
                )

            if t == ActionType.SEARCH:
                if p.get("chat_id") is not None:
                    return await self.tg.search_messages(
                        chat_id=int(p["chat_id"]),
                        query=str(p["query"]),
                        limit=int(p.get("limit", 20)),
                        from_user=p.get("from_user"),
                    )
                return await self.tg.search_messages_global(
                    query=str(p["query"]),
                    limit=int(p.get("limit", 20)),
                    from_user=p.get("from_user"),
                )

            if t == ActionType.FORWARD:
                return await self.tg.forward_message(
                    from_chat_id=int(p["from_chat_id"]),
                    message_id=int(p["message_id"]),
                    to_chat_id=int(p["to_chat_id"]),
                    to_topic_id=p.get("to_topic_id"),
                )

            if t == ActionType.PIN:
                return await self.tg.pin_message(
                    chat_id=int(p["chat_id"]),
                    message_id=int(p["message_id"]),
                )

            if t == ActionType.GET_CHAT_CONTEXT:
                return await self.tg.get_chat_context(
                    chat_id=int(p["chat_id"]),
                    limit=int(p.get("limit", 30)),
                )

            if t == ActionType.GET_TOPIC_CONTEXT:
                return await self.tg.get_topic_context(
                    chat_id=int(p["chat_id"]),
                    topic_id=int(p["topic_id"]),
                    limit=int(p.get("limit", 30)),
                )

            if t == ActionType.LIST_CHATS:
                chats = await self.tg.list_available_chats(
                    limit=int(p.get("limit", 30)),
                    query=p.get("query", ""),
                )
                return {"chats": chats}

            if t == ActionType.LIST_TOPICS:
                return await self.tg.list_forum_topics(
                    chat_id=int(p["chat_id"]),
                    limit=int(p.get("limit", 20)),
                    query=p.get("query", ""),
                )

            if t == ActionType.LIST_MEMBERS:
                return await self.tg.list_chat_members(
                    chat_id=int(p["chat_id"]),
                    query=p.get("query", ""),
                    limit=int(p.get("limit", 20)),
                )

            if t == ActionType.LIST_TOPIC_PARTICIPANTS:
                return await self.tg.list_topic_participants(
                    chat_id=int(p["chat_id"]),
                    topic_id=int(p["topic_id"]),
                    query=p.get("query", ""),
                )

            if t == ActionType.USER_INFO:
                return await self.tg.get_user_info(target=str(p["target"]))

            if t == ActionType.SUMMARIZE:
                return await self._summarize(p, session_key)

            if t == ActionType.RESPOND_TEXT:
                return {"text": p.get("text", ""), "needs_llm": p.get("needs_llm_response", False)}

            return {"error": f"unhandled action: {t.value}"}

        except Exception as e:
            log.error("Action %s failed: %s", t.value, e)
            return {"error": f"{type(e).__name__}: {e}"}

    async def _summarize(self, params: dict, session_key: str) -> dict[str, Any]:
        """Get context and summarize via LLM."""
        chat_id = int(params["chat_id"])
        topic_id = params.get("topic_id")
        limit = int(params.get("limit", 30))

        if topic_id:
            ctx = await self.tg.get_topic_context(chat_id, int(topic_id), limit)
        else:
            ctx = await self.tg.get_chat_context(chat_id, limit)

        messages = ctx.get("messages", [])
        if not messages:
            return {"text": "Нет сообщений для резюме."}

        messages_text = "\n".join(
            f"[{m.get('date', '?')}] {(m.get('sender') or {}).get('name', '?')}: {m.get('text', '')}"
            for m in messages
        )

        summary = await call_llm(
            system=SUMMARIZE_MESSAGES.format(messages=messages_text),
            user="Подведи итоги.",
            session_key=session_key + "_summarize",
        )
        return {"text": summary or "Не удалось получить резюме."}
