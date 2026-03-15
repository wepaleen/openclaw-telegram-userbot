"""Normalization helpers for Telethon entities, messages and events."""

from typing import Any

from telethon import events
from telethon.tl import types
from telethon.tl.custom.message import Message

from shared.schemas.telegram import InboundTelegramEvent, PeerRef, PeerType


def display_name(entity: Any) -> str | None:
    title = getattr(entity, "title", None)
    if title:
        return title
    first_name = getattr(entity, "first_name", None)
    last_name = getattr(entity, "last_name", None)
    username = getattr(entity, "username", None)
    if first_name or last_name:
        return " ".join(x for x in [first_name, last_name] if x).strip()
    return f"@{username}" if username else None


def peer_ref_from_entity(entity: Any) -> PeerRef:
    """Convert a Telethon entity to a stable peer reference."""
    if isinstance(entity, types.User):
        peer_type = PeerType.USER
    elif isinstance(entity, types.Chat):
        peer_type = PeerType.CHAT
    else:
        peer_type = PeerType.CHANNEL

    return PeerRef(
        peer_type=peer_type,
        peer_id=int(getattr(entity, "id")),
        access_hash=getattr(entity, "access_hash", None),
        username=getattr(entity, "username", None),
        title=display_name(entity),
    )


def serialize_dialog_entity(entity: Any) -> dict[str, Any]:
    """Serialize a Telethon dialog entity into an index-friendly chat row."""
    peer = peer_ref_from_entity(entity)
    username = getattr(entity, "username", None)
    return {
        "chat_id": peer.peer_id,
        "title": peer.title or str(peer.peer_id),
        "username": f"@{username}" if username else None,
        "type": peer.peer_type.value,
        "peer_type": peer.peer_type.value,
        "is_forum": bool(getattr(entity, "forum", False)),
        "is_bot": bool(getattr(entity, "bot", False)),
    }


def serialize_member_entity(entity: Any) -> dict[str, Any]:
    """Serialize a user/channel participant into a compact member row."""
    username = getattr(entity, "username", None)
    return {
        "id": int(getattr(entity, "id")),
        "name": display_name(entity) or username or str(getattr(entity, "id")),
        "username": f"@{username}" if username else None,
        "peer": {
            "peer_type": peer_ref_from_entity(entity).peer_type.value,
            "peer_id": int(getattr(entity, "id")),
            "access_hash": getattr(entity, "access_hash", None),
            "username": username,
            "title": display_name(entity),
        },
    }


def _reply_metadata(message: Message) -> tuple[int | None, int | None, bool]:
    reply = getattr(message, "reply_to", None)
    reply_to_msg_id = getattr(reply, "reply_to_msg_id", None)
    top_msg_id = getattr(reply, "reply_to_top_id", None)
    is_topic_message = bool(
        top_msg_id
        or getattr(reply, "forum_topic", False)
        or getattr(message, "forum_topic", False)
    )
    return reply_to_msg_id, top_msg_id, is_topic_message


def serialize_message(
    message: Message,
    chat_entity: Any | None = None,
    sender_entity: Any | None = None,
) -> dict[str, Any]:
    """Serialize a Telethon message into a transport-safe dict."""
    chat = chat_entity or getattr(message, "chat", None)
    sender = sender_entity or getattr(message, "sender", None)
    reply_to_msg_id, top_msg_id, is_topic_message = _reply_metadata(message)

    sender_name = None
    sender_username = None
    if sender is not None:
        sender_name = display_name(sender)
        raw_username = getattr(sender, "username", None)
        sender_username = f"@{raw_username}" if raw_username else None

    return {
        "id": int(message.id),
        "peer_id": int(getattr(chat, "id", 0)) if chat is not None else None,
        "chat_title": display_name(chat) if chat is not None else None,
        "date_utc": message.date.isoformat() if getattr(message, "date", None) else None,
        "sender_id": int(getattr(sender, "id")) if sender is not None and getattr(sender, "id", None) else None,
        "sender_name": sender_name,
        "sender_username": sender_username,
        "text": (message.message or "").strip(),
        "reply_to_msg_id": reply_to_msg_id,
        "top_msg_id": top_msg_id,
        "is_topic_message": is_topic_message,
    }


async def normalize_new_message_event(
    event: events.NewMessage.Event,
    account_id: str,
    chat_entity: Any | None = None,
    sender_entity: Any | None = None,
) -> InboundTelegramEvent:
    """Convert a Telethon NewMessage event into the shared inbound contract."""
    chat = chat_entity or await event.get_chat()
    sender = sender_entity
    if sender is None and getattr(event, "sender_id", None) is not None:
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None

    reply_to_msg_id, top_msg_id, is_topic_message = _reply_metadata(event.message)
    return InboundTelegramEvent(
        event_id=f"{account_id}:{getattr(chat, 'id', 'unknown')}:{event.message.id}",
        account_id=account_id,
        peer=peer_ref_from_entity(chat),
        sender_id=getattr(event, "sender_id", None),
        sender_username=(
            f"@{getattr(sender, 'username')}"
            if sender is not None and getattr(sender, "username", None)
            else None
        ),
        message_id=int(event.message.id),
        text=(event.raw_text or "").strip(),
        date_utc=event.message.date,
        reply_to_msg_id=reply_to_msg_id,
        top_msg_id=top_msg_id,
        is_topic_message=is_topic_message,
        raw_context_ref=str(getattr(event, "chat_id", "")),
        metadata={
            "outgoing": "1" if getattr(event, "out", False) else "0",
            "chat_title": display_name(chat) or "",
        },
    )
