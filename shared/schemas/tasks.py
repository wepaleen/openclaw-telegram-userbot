"""Typed task-core contracts shared between agent runtime and task service."""

from dataclasses import dataclass

from shared.schemas.telegram import PeerRef


@dataclass(slots=True)
class ParsedTimeResult:
    """Normalized result of human time parsing."""

    original_text: str
    remind_at_local: str
    remind_at_utc: str
    timezone: str
    confidence: float
    requires_confirmation: bool = False


@dataclass(slots=True)
class ReminderTarget:
    """Telegram target information stored alongside reminders."""

    target_peer: PeerRef
    reply_to_msg_id: int | None = None
    top_msg_id: int | None = None


@dataclass(slots=True)
class TaskCreateRequest:
    """Task creation payload that is stable across runtimes."""

    title: str
    description: str | None = None
    assignee_peer: PeerRef | None = None
    due_at_utc: str | None = None
    timezone: str | None = None
    origin_peer: PeerRef | None = None
    origin_message_id: int | None = None
    origin_sender_id: int | None = None
