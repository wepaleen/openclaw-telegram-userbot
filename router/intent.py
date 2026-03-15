"""Intent classifier: regex first pass + LLM fallback."""

import json
import logging
from dataclasses import dataclass

from executor.actions import ActionType, Action
from router.patterns import match_intent
from planner.llm_client import call_llm, extract_json
from planner.prompts import INTENT_CLASSIFY

log = logging.getLogger("intent")

# Map LLM intent strings to ActionType
_INTENT_MAP: dict[str, ActionType] = {
    "send_private": ActionType.SEND_PRIVATE,
    "send_chat": ActionType.SEND_CHAT,
    "send_topic": ActionType.SEND_TOPIC,
    "send_link": ActionType.SEND_LINK,
    "search_messages": ActionType.SEARCH,
    "forward_message": ActionType.FORWARD,
    "pin_message": ActionType.PIN,
    "summarize": ActionType.SUMMARIZE,
    "create_reminder": ActionType.CREATE_REMINDER,
    "cancel_reminder": ActionType.CANCEL_REMINDER,
    "list_reminders": ActionType.LIST_REMINDERS,
    "create_task": ActionType.CREATE_TASK,
    "update_task": ActionType.UPDATE_TASK,
    "list_tasks": ActionType.LIST_TASKS,
    "schedule_message": ActionType.SCHEDULE_MESSAGE,
    "get_chat_context": ActionType.GET_CHAT_CONTEXT,
    "get_topic_context": ActionType.GET_TOPIC_CONTEXT,
    "list_chats": ActionType.LIST_CHATS,
    "list_topics": ActionType.LIST_TOPICS,
    "list_chat_members": ActionType.LIST_MEMBERS,
    "get_user_info": ActionType.USER_INFO,
    "add_contact": ActionType.ADD_CONTACT,
    "list_contacts": ActionType.LIST_CONTACTS,
    "respond_text": ActionType.RESPOND_TEXT,
}


async def classify_intent(
    text: str,
    contacts_summary: str = "[]",
    chats_summary: str = "[]",
    session_key: str = "",
) -> Action:
    """Classify user intent. Tries regex first, falls back to LLM."""

    # Pass 1: regex
    match = match_intent(text)
    if match and match.confidence >= 0.8:
        log.info("Regex match: %s (confidence=%.1f)", match.intent, match.confidence)
        return Action(
            type=match.intent,
            params=match.params,
            source="regex",
        )

    # Pass 2: LLM classification
    log.info("No regex match, falling back to LLM classification")
    prompt = INTENT_CLASSIFY.format(
        contacts=contacts_summary,
        chats=chats_summary,
    )

    try:
        response = await call_llm(
            system=prompt,
            user=text,
            max_tokens=512,
            session_key=session_key + "_classify",
        )
        parsed = extract_json(response)

        if parsed and "intent" in parsed:
            intent_str = parsed["intent"]
            action_type = _INTENT_MAP.get(intent_str, ActionType.RESPOND_TEXT)
            params = parsed.get("params", {})
            confidence = float(parsed.get("confidence", 0.5))
            log.info("LLM classified: %s (confidence=%.2f)", action_type, confidence)

            # If LLM says respond_text with actual text, use it
            if action_type == ActionType.RESPOND_TEXT and "text" in params:
                return Action(
                    type=ActionType.RESPOND_TEXT,
                    params=params,
                    source="llm",
                )

            return Action(
                type=action_type,
                params=params,
                source="llm",
            )
    except Exception as e:
        log.warning("LLM classification failed: %s", e)

    # Fallback: treat as general question, let LLM answer
    return Action(
        type=ActionType.RESPOND_TEXT,
        params={"needs_llm_response": True, "raw_text": text},
        source="fallback",
    )
