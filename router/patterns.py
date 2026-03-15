"""Regex patterns for intent classification (first pass)."""

import re
from dataclasses import dataclass

from executor.actions import ActionType


@dataclass
class PatternMatch:
    intent: ActionType
    params: dict
    confidence: float = 1.0


# --- DM patterns ---
DM_PATTERNS = [
    re.compile(
        r"^\s*/(?:dm|pm)\s+(?P<target>@[A-Za-z0-9_]+|-?\d+)\s+(?P<text>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^\s*(?:напиши|отправь|пошли|скинь)\s+(?:в\s+(?:личку|лс|лк|дм|dm|pm)\s+)?(?P<target>@[A-Za-z0-9_]+|-?\d+)\s*(?:[:,\-]\s*|\s+)(?P<text>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^\s*(?:напиши|отправь|пошли|скинь)\s+(?P<target>@[A-Za-z0-9_]+)\s+(?P<text>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
]

# --- Link patterns ---
LINK_PATTERNS = [
    re.compile(
        r"^\s*/(?:sendlink|send_to_link|sl)\s+(?P<link>(?:https?://)?t\.me/\S+|tg://\S+)\s+(?P<text>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^\s*(?:напиши|отправь)\s+(?:сюда|туда|по\s+ссылке)?\s*(?P<link>(?:https?://)?t\.me/\S+|tg://\S+)\s*(?:[:,\-]\s*|\s+)(?P<text>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
]

# --- Topic send patterns ---
TOPIC_PATTERNS = [
    re.compile(
        r"^\s*/(?:topic_send|ts)\s+(?:(?P<chat_id>-?\d+)\s+)?(?P<topic_id>\d+)\s+(?:(?P<mention>@[A-Za-z0-9_]+)\s+)?(?P<text>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^\s*/(?:topic_send|ts)\s+(?:(?P<chat_id>-?\d+)\s+)?(?P<topic_ref>.+?)(?:\s+(?P<mention>@[A-Za-z0-9_]+))?\s*:\s*(?P<text>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
]

# --- Search patterns ---
SEARCH_PATTERNS = [
    re.compile(
        r"^\s*(?:найди|поиск|search|искать)\s+(?:в\s+)?(?:чат[еу]?\s+)?(?:['\"«](?P<query>.+?)['\»\"»]|(?P<query2>\S+))",
        re.IGNORECASE,
    ),
]

# --- Remind patterns ---
REMIND_PATTERNS = [
    re.compile(
        r"^\s*(?:напомни|remind)\s+(?:мне\s+)?(?:через\s+(?P<delta>\d+\s*(?:мин|час|минут|hours?|minutes?|ч|м)))?(?:\s*в\s+(?P<time>\d{1,2}[:.]\d{2}))?\s*(?:[:,\-\s]+)?(?P<text>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
]

# --- Task patterns ---
TASK_PATTERNS = [
    re.compile(
        r"^\s*(?:задач[аиу]|таск|task|todo)\s+(?:созд[ай]|add|создать|new)\s*[:,\-\s]+(?P<title>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^\s*(?:создай|добавь)\s+(?:задач[уи]|таск|task)\s*[:,\-\s]+(?P<title>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
]

TASK_LIST_PATTERNS = [
    re.compile(
        r"^\s*(?:задачи|таски|tasks|todos?|мои задачи|список задач|покажи задачи)",
        re.IGNORECASE,
    ),
]

# --- Forward patterns ---
FORWARD_PATTERNS = [
    re.compile(
        r"^\s*(?:перешли|forward|переслать)\s+",
        re.IGNORECASE,
    ),
]

# --- Summarize patterns ---
SUMMARIZE_PATTERNS = [
    re.compile(
        r"^\s*(?:подведи\s+итог|резюме|итоги|summarize|summary|что\s+нового|что\s+пишут|о\s+чём\s+(?:говорят|писали|пишут))",
        re.IGNORECASE,
    ),
]

# --- List chats patterns ---
LIST_CHATS_PATTERNS = [
    re.compile(
        r"^\s*(?:покажи|список|list)\s+(?:чат[ыов]?|chat[s]?|групп[ыу]?)",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*/chats?\s*$", re.IGNORECASE),
]

# --- Read context patterns ---
READ_CONTEXT_PATTERNS = [
    re.compile(
        r"^\s*(?:прочитай|покажи|read|show|что\s+в)\s+(?:чат[еу]?\s+|chat\s+)?",
        re.IGNORECASE,
    ),
]

# --- Contact add patterns ---
CONTACT_ADD_PATTERNS = [
    re.compile(
        r"^\s*(?:запомни|добавь\s+контакт|contact\s+add)\s+(?P<name>.+?)\s*(?:=|это|is)\s*(?P<target>@[A-Za-z0-9_]+|-?\d+)\s*$",
        re.IGNORECASE,
    ),
]

# --- Contact list patterns ---
CONTACT_LIST_PATTERNS = [
    re.compile(
        r"^\s*(?:контакты|contacts?|адресная\s+книга|список\s+контактов)",
        re.IGNORECASE,
    ),
]

# --- Pin patterns ---
PIN_PATTERNS = [
    re.compile(
        r"^\s*(?:закрепи|pin)\s+",
        re.IGNORECASE,
    ),
]

# --- User info patterns ---
USER_INFO_PATTERNS = [
    re.compile(
        r"^\s*(?:кто\s+(?:такой|такая|это)|who\s+is|инфо\s+о|info)\s+(?P<target>@[A-Za-z0-9_]+|\S+)",
        re.IGNORECASE,
    ),
]


def match_intent(text: str) -> PatternMatch | None:
    """Try to match user text against all regex patterns.
    Returns PatternMatch on first match, None if nothing matches.
    """
    stripped = text.strip()

    # DM
    for p in DM_PATTERNS:
        m = p.match(stripped)
        if m:
            return PatternMatch(
                intent=ActionType.SEND_PRIVATE,
                params={"target": m.group("target").strip(), "text": m.group("text").strip()},
            )

    # Link
    for p in LINK_PATTERNS:
        m = p.match(stripped)
        if m:
            return PatternMatch(
                intent=ActionType.SEND_LINK,
                params={"link": m.group("link").strip(), "text": m.group("text").strip()},
            )

    # Topic send
    for p in TOPIC_PATTERNS:
        m = p.match(stripped)
        if m:
            d = m.groupdict()
            params: dict = {"text": (d.get("text") or "").strip()}
            if d.get("topic_id"):
                params["topic_id"] = int(d["topic_id"])
            elif d.get("topic_ref"):
                params["topic_ref"] = d["topic_ref"].strip()
            if d.get("chat_id"):
                params["chat_id"] = d["chat_id"].strip()
            if d.get("mention"):
                params["mention_username"] = d["mention"].strip()
            return PatternMatch(intent=ActionType.SEND_TOPIC, params=params)

    # Task create
    for p in TASK_PATTERNS:
        m = p.match(stripped)
        if m:
            return PatternMatch(
                intent=ActionType.CREATE_TASK,
                params={"title": m.group("title").strip()},
            )

    # Task list
    for p in TASK_LIST_PATTERNS:
        if p.match(stripped):
            return PatternMatch(intent=ActionType.LIST_TASKS, params={})

    # Remind
    for p in REMIND_PATTERNS:
        m = p.match(stripped)
        if m:
            d = m.groupdict()
            params = {"text": (d.get("text") or "").strip()}
            if d.get("delta"):
                params["delta"] = d["delta"].strip()
            if d.get("time"):
                params["time"] = d["time"].strip()
            return PatternMatch(intent=ActionType.CREATE_REMINDER, params=params)

    # Summarize
    for p in SUMMARIZE_PATTERNS:
        if p.search(stripped):
            return PatternMatch(intent=ActionType.SUMMARIZE, params={"raw_text": stripped})

    # Contact add
    for p in CONTACT_ADD_PATTERNS:
        m = p.match(stripped)
        if m:
            return PatternMatch(
                intent=ActionType.ADD_CONTACT,
                params={"name": m.group("name").strip(), "target": m.group("target").strip()},
            )

    # Contact list
    for p in CONTACT_LIST_PATTERNS:
        if p.match(stripped):
            return PatternMatch(intent=ActionType.LIST_CONTACTS, params={})

    # List chats
    for p in LIST_CHATS_PATTERNS:
        if p.match(stripped):
            return PatternMatch(intent=ActionType.LIST_CHATS, params={})

    # Forward
    for p in FORWARD_PATTERNS:
        if p.match(stripped):
            return PatternMatch(
                intent=ActionType.FORWARD, params={"raw_text": stripped}, confidence=0.6,
            )

    # Pin
    for p in PIN_PATTERNS:
        if p.match(stripped):
            return PatternMatch(
                intent=ActionType.PIN, params={"raw_text": stripped}, confidence=0.6,
            )

    # User info
    for p in USER_INFO_PATTERNS:
        m = p.match(stripped)
        if m:
            return PatternMatch(
                intent=ActionType.USER_INFO,
                params={"target": m.group("target").strip()},
            )

    # Search
    for p in SEARCH_PATTERNS:
        m = p.match(stripped)
        if m:
            q = (m.groupdict().get("query") or m.groupdict().get("query2") or "").strip()
            return PatternMatch(
                intent=ActionType.SEARCH, params={"query": q, "raw_text": stripped},
            )

    return None
