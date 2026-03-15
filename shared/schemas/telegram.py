"""Typed Telegram transport contracts for the future Telethon/OpenClaw bridge."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class PeerType(str, Enum):
    USER = "user"
    CHAT = "chat"
    CHANNEL = "channel"


@dataclass(slots=True, frozen=True)
class PeerRef:
    """Stable peer reference passed between transport and domain services."""

    peer_type: PeerType
    peer_id: int
    access_hash: int | None = None
    username: str | None = None
    title: str | None = None


@dataclass(slots=True)
class InboundTelegramEvent:
    """Normalized inbound Telegram update emitted by the transport service."""

    event_id: str
    account_id: str
    peer: PeerRef
    sender_id: int | None
    sender_username: str | None
    message_id: int
    text: str
    date_utc: datetime
    reply_to_msg_id: int | None = None
    reply_to_sender_id: int | None = None
    top_msg_id: int | None = None
    is_topic_message: bool = False
    raw_context_ref: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        top = self.top_msg_id or 0
        return f"tg:{self.peer.peer_type.value}:{self.peer.peer_id}:thread:{top}"


@dataclass(slots=True)
class OutboundTelegramCommand:
    """Transport command that can be executed by the Telethon bridge."""

    target_peer: PeerRef
    text: str
    reply_to_msg_id: int | None = None
    top_msg_id: int | None = None
    parse_mode: str | None = None
    idempotency_key: str | None = None
    disable_link_preview: bool = False


@dataclass(slots=True)
class ResolvedRecipient:
    """Recipient resolution result returned by a resolver tool."""

    peer: PeerRef
    confidence: float
    source: str
    needs_confirmation: bool = False
    display_label: str | None = None


@dataclass(slots=True)
class ResolvedTargetContext:
    """Resolved Telegram target context for a future send/reply action."""

    peer: PeerRef
    reply_to_msg_id: int | None = None
    top_msg_id: int | None = None
    is_topic_message: bool = False
    source: str = "current_context"
