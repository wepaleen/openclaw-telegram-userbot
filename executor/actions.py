"""Action type definitions."""

from dataclasses import dataclass, field
from enum import Enum


class ActionType(Enum):
    SEND_PRIVATE = "send_private"
    SEND_CHAT = "send_chat"
    SEND_TOPIC = "send_topic"
    SEND_LINK = "send_link"
    SEARCH = "search_messages"
    FORWARD = "forward_message"
    PIN = "pin_message"
    GET_CHAT_CONTEXT = "get_chat_context"
    GET_TOPIC_CONTEXT = "get_topic_context"
    LIST_CHATS = "list_chats"
    LIST_TOPICS = "list_topics"
    LIST_MEMBERS = "list_chat_members"
    LIST_TOPIC_PARTICIPANTS = "list_topic_participants"
    USER_INFO = "get_user_info"
    SUMMARIZE = "summarize"
    CREATE_REMINDER = "create_reminder"
    CANCEL_REMINDER = "cancel_reminder"
    LIST_REMINDERS = "list_reminders"
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    LIST_TASKS = "list_tasks"
    SCHEDULE_MESSAGE = "schedule_message"
    ADD_CONTACT = "add_contact"
    LIST_CONTACTS = "list_contacts"
    RESPOND_TEXT = "respond_text"
    CONFIRM_PENDING = "confirm_pending"


class RiskLevel(Enum):
    READ = "read"
    WRITE = "write"
    RISKY = "risky"


RISK_MAP: dict[ActionType, RiskLevel] = {
    ActionType.GET_CHAT_CONTEXT: RiskLevel.READ,
    ActionType.GET_TOPIC_CONTEXT: RiskLevel.READ,
    ActionType.LIST_CHATS: RiskLevel.READ,
    ActionType.LIST_TOPICS: RiskLevel.READ,
    ActionType.LIST_MEMBERS: RiskLevel.READ,
    ActionType.LIST_TOPIC_PARTICIPANTS: RiskLevel.READ,
    ActionType.SEARCH: RiskLevel.READ,
    ActionType.USER_INFO: RiskLevel.READ,
    ActionType.LIST_CONTACTS: RiskLevel.READ,
    ActionType.LIST_TASKS: RiskLevel.READ,
    ActionType.LIST_REMINDERS: RiskLevel.READ,
    ActionType.RESPOND_TEXT: RiskLevel.READ,
    ActionType.SUMMARIZE: RiskLevel.READ,

    ActionType.SEND_PRIVATE: RiskLevel.WRITE,
    ActionType.SEND_CHAT: RiskLevel.WRITE,
    ActionType.SEND_TOPIC: RiskLevel.WRITE,
    ActionType.SEND_LINK: RiskLevel.WRITE,
    ActionType.PIN: RiskLevel.WRITE,
    ActionType.CREATE_REMINDER: RiskLevel.WRITE,
    ActionType.CREATE_TASK: RiskLevel.WRITE,
    ActionType.UPDATE_TASK: RiskLevel.WRITE,
    ActionType.SCHEDULE_MESSAGE: RiskLevel.WRITE,
    ActionType.ADD_CONTACT: RiskLevel.WRITE,

    ActionType.FORWARD: RiskLevel.RISKY,
}


@dataclass
class Action:
    type: ActionType
    params: dict = field(default_factory=dict)
    source: str = "regex"  # "regex" | "llm" | "planner"
    confirmation_text: str | None = None

    @property
    def risk(self) -> RiskLevel:
        return RISK_MAP.get(self.type, RiskLevel.WRITE)
