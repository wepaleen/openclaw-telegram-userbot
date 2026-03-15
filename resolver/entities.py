"""Entity resolver: resolves human names, chat names, topics to concrete IDs."""

import logging
import re
from typing import Any

from resolver.contacts import search_contacts
from resolver.chats import search_chats, search_topics
from config import normalize_chat_id

log = logging.getLogger("resolver")


class ResolutionError(ValueError):
    """Raised when an entity cannot be resolved safely."""


async def resolve_target(target: str) -> str | int:
    """Resolve a target that might be a name, @username, or user_id."""
    value = target.strip()

    # Already a username or numeric ID
    if value.startswith("@"):
        return value
    if value.lstrip("-").isdigit():
        return int(value)

    # Try contact book
    matches = await search_contacts(value)
    if len(matches) == 1:
        contact = matches[0]
        if contact.get("username"):
            resolved = contact["username"]
        elif contact.get("user_id"):
            resolved = int(contact["user_id"])
        else:
            raise ResolutionError(
                f"контакт «{contact['display_name']}» найден, но у него нет username или user id"
            )
        log.info("Resolved '%s' -> %s via contact book", value, resolved)
        return resolved
    if len(matches) > 1:
        variants = ", ".join(
            c.get("display_name") or c.get("username") or str(c.get("id"))
            for c in matches[:5]
        )
        raise ResolutionError(
            f"контакт «{value}» неоднозначен: {variants}"
        )

    # Fallback: treat username-like values as @username, but do not guess on human names.
    if re.fullmatch(r"[A-Za-z0-9_]{3,32}", value):
        return f"@{value}"
    raise ResolutionError(f"контакт «{value}» не найден")


async def resolve_chat_id(chat_name: str) -> int | None:
    """Resolve a chat name/title to chat_id."""
    value = chat_name.strip()

    # Already numeric
    if value.lstrip("-").isdigit():
        return normalize_chat_id(value)

    # Search chat index
    chats = await search_chats(value)
    if len(chats) == 1:
        chat = chats[0]
        log.info("Resolved chat '%s' -> %d", value, chat["chat_id"])
        return chat["chat_id"]
    if len(chats) > 1:
        variants = ", ".join(
            f"{c.get('title', '?')} ({c['chat_id']})"
            for c in chats[:5]
        )
        raise ResolutionError(f"чат «{value}» неоднозначен: {variants}")

    return None


async def resolve_topic(chat_id: int, topic_name: str) -> dict[str, Any] | None:
    """Resolve a topic name/ID within a chat."""
    topics = await search_topics(chat_id, topic_name)
    if len(topics) == 1:
        topic = topics[0]
        log.info("Resolved topic '%s' in chat %d -> %d", topic_name, chat_id, topic["topic_id"])
        return topic
    if len(topics) > 1:
        variants = ", ".join(
            f"{t.get('title', '?')} ({t['topic_id']})"
            for t in topics[:5]
        )
        raise ResolutionError(f"тема «{topic_name}» неоднозначна: {variants}")
    return None


async def resolve_action_params(
    action_type: str,
    params: dict[str, Any],
    fallback_chat_id: int | None = None,
) -> dict[str, Any]:
    """Resolve human-readable names in action params to concrete IDs.
    Returns updated params dict.
    """
    resolved = dict(params)

    # Resolve 'target' field (for send_private, user_info)
    if "target" in resolved and not str(resolved["target"]).startswith("@"):
        target_val = str(resolved["target"])
        if not target_val.lstrip("-").isdigit():
            target = await resolve_target(target_val)
            resolved["target"] = target

    # Resolve 'chat_name' to 'chat_id'
    if "chat_name" in resolved and "chat_id" not in resolved:
        chat_id = await resolve_chat_id(str(resolved["chat_name"]))
        if chat_id:
            resolved["chat_id"] = chat_id
        else:
            raise ResolutionError(f"чат «{resolved['chat_name']}» не найден")
        del resolved["chat_name"]

    # Ensure chat_id exists for actions that need it
    if "chat_id" not in resolved and fallback_chat_id:
        if action_type in {
            "send_chat", "get_chat_context",
            "get_topic_context", "list_topics", "list_chat_members",
            "send_topic", "summarize",
        }:
            resolved["chat_id"] = fallback_chat_id

    # Resolve 'topic_name'/'topic_ref' to 'topic_id'
    if ("topic_name" in resolved or "topic_ref" in resolved) and "topic_id" not in resolved:
        topic_name = resolved.pop("topic_name", None) or resolved.pop("topic_ref", None)
        if topic_name:
            chat_id = resolved.get("chat_id", fallback_chat_id)
            if chat_id:
                topic = await resolve_topic(chat_id, str(topic_name))
                if topic:
                    resolved["topic_id"] = topic["topic_id"]
                else:
                    raise ResolutionError(f"тема «{topic_name}» не найдена")
            else:
                raise ResolutionError("нельзя разрешить тему без chat_id")

    return resolved
