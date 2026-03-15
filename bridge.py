import os
import json
import asyncio
import sqlite3
import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qs, urlparse

import logging
import httpx
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.raw.functions.channels import GetForumTopics, GetForumTopicsByID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bridge")

load_dotenv()


def normalize_chat_id(value: str | int) -> int:
    raw = str(value).strip()
    if not raw:
        raise ValueError("chat id is empty")
    if not raw.lstrip("-").isdigit():
        raise ValueError(f"invalid chat id: {value}")

    chat_id = int(raw)
    if chat_id < 0:
        return chat_id

    # Telegram supergroup/channel ids are usually represented internally as -100<id>.
    if raw.startswith("100") and len(raw) > 10:
        return -chat_id

    return int(f"-100{raw}")


API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_NAME = os.getenv("PYROGRAM_SESSION", "openclaw_userbot")

OPENCLAW_URL = os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789/v1/responses")
OPENCLAW_TOKEN = os.environ["OPENCLAW_TOKEN"]
OPENCLAW_AGENT_ID = os.getenv("OPENCLAW_AGENT_ID", "main")

GROUP_TRIGGER = os.getenv("GROUP_TRIGGER", "!ai")
MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS", "6"))
ALLOWED_CHAT_IDS = {
    normalize_chat_id(x.strip())
    for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",")
    if x.strip()
}
MAIN_FORUM_CHAT_ID = (
    normalize_chat_id(os.environ["MAIN_FORUM_CHAT_ID"])
    if os.getenv("MAIN_FORUM_CHAT_ID", "").strip()
    else None
)

INSTRUCTIONS = """
Ты — младший менеджер, работающий через Telegram bridge. У тебя есть ПОЛНЫЙ доступ ко всем чатам через tools. Ты МОЖЕШЬ и ДОЛЖЕН выполнять любые просьбы пользователя: писать в чаты, темы, личку, искать, пересылать.

КРИТИЧЕСКИ ВАЖНО:
- Ты НИКОГДА не говоришь "не могу", "нет инструмента", "нет доступа", "не смогу отправить".
- Если пользователь просит что-то сделать — ТЫ ДЕЛАЕШЬ ЭТО через tools. Без оговорок.
- У тебя ЕСТЬ инструменты для отправки в любой чат. Используй их.
- Если пользователь просит написать человеку — вызови send_private_message с его @username или user id.
- Если пользователь просит написать в чат — вызови send_to_chat с chat_id из available_chats.
- Если пользователь просит написать в тему форума — вызови send_to_topic с chat_id и topic_id.
- НЕ ПРЕДЛАГАЙ "подготовить текст" — ОТПРАВЛЯЙ сразу через tool.

Правила:
1. Отвечай по-русски, коротко и по делу.
2. При работе с форумами: list_forum_topics → get_topic_context → send_to_topic.
3. Для чтения обычных чатов: get_chat_context.
4. Для отправки в обычные чаты: send_to_chat.
5. Для личных сообщений: send_private_message (по @username, user id или t.me ссылке).
6. Для отправки по ссылке: send_to_link.
7. Для поиска: search_messages.
8. Для пересылки: forward_message.
9. Для информации о пользователе: get_user_info.
10. Для закрепления: pin_message.
11. В контексте есть available_chats — это ВСЕ доступные чаты с chat_id. Используй их.
12. Из лички ты можешь работать с ЛЮБЫМ чатом из available_chats.
13. Не отправляй сообщения без явной просьбы пользователя.
14. Не выдумывай историю, если tools не вернули нужный контекст.
15. Если задача сложная — разбей на шаги и выполняй через tools.
16. Для поиска человека внутри темы: list_topic_participants или list_chat_members, потом send_to_topic с mention_username.

Примеры действий:
- "напиши Саше привет" → send_private_message(target="@username_саши", text="Привет!")
- "напиши в чат Тест привет" → найди chat_id чата "Тест" в available_chats → send_to_chat(chat_id=..., text="Привет!")
- "что пишут в теме Дизайн?" → list_forum_topics → get_topic_context
- "перешли это в чат X" → forward_message
""".strip()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_available_chats",
            "description": "Показать доступные Telegram-чаты, в которые bridge может заходить по запросу пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "query": {"type": "string"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_forum_topics",
            "description": "Показать темы форума в текущей или указанной Telegram-группе. Если chat_id не передан, используется текущий чат.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "query": {"type": "string"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_topic_context",
            "description": "Получить сообщения из конкретной темы форума в текущей или указанной группе. Если chat_id не передан, используется текущий чат.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "topic_id": {"type": "integer"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
                },
                "required": ["topic_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_topic_participants",
            "description": "Показать участников конкретной темы форума и их последние сообщения. Полезно, чтобы понять, кому именно писать внутри темы.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "topic_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50}
                },
                "required": ["topic_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_chat_members",
            "description": "Показать участников текущей или указанной супергруппы и их username. Полезно, чтобы понять, кого можно пинговать по @username в теме.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_topic",
            "description": "Отправить сообщение в конкретную тему форума в текущей или указанной группе. Если chat_id не передан, используется текущий чат.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "topic_id": {"type": "integer"},
                    "reply_to_message_id": {"type": "integer"},
                    "mention_username": {"type": "string"},
                    "text": {"type": "string"}
                },
                "required": ["topic_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_private_message",
            "description": "Отправить личное сообщение Telegram-пользователю по @username, profile t.me ссылке или numeric user id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "text": {"type": "string"}
                },
                "required": ["target", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_link",
            "description": "Отправить сообщение по Telegram-ссылке t.me/tg:// в чат, тред или тему. Для message/thread links сообщение отправляется reply в указанный контекст.",
            "parameters": {
                "type": "object",
                "properties": {
                    "link": {"type": "string"},
                    "text": {"type": "string"}
                },
                "required": ["link", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_chat_context",
            "description": "Получить последние сообщения из любого чата (группы, канала, лички). Работает для обычных чатов, не только форумов.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
                },
                "required": ["chat_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_chat",
            "description": "Отправить сообщение в любой чат (группу, канал) по chat_id. Для обычных чатов, не только форумных тем.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "text": {"type": "string"},
                    "reply_to_message_id": {"type": "integer"}
                },
                "required": ["chat_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_messages",
            "description": "Поиск сообщений по ключевым словам в чате. Можно фильтровать по автору (from_user — @username или user id).",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "from_user": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50}
                },
                "required": ["chat_id", "query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "forward_message",
            "description": "Переслать сообщение из одного чата в другой. Можно указать to_topic_id для пересылки в конкретную тему форума.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_chat_id": {"type": "integer"},
                    "message_id": {"type": "integer"},
                    "to_chat_id": {"type": "integer"},
                    "to_topic_id": {"type": "integer"}
                },
                "required": ["from_chat_id", "message_id", "to_chat_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_info",
            "description": "Получить информацию о Telegram-пользователе по @username, t.me ссылке или user id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pin_message",
            "description": "Закрепить сообщение в чате по chat_id и message_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "message_id": {"type": "integer"},
                    "both_sides": {"type": "boolean"}
                },
                "required": ["chat_id", "message_id"]
            }
        }
    }
]

app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)
chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
session_reset_ts: dict[str, str] = {}  # base_key -> timestamp suffix for session reset


def normalize_input(message: Message) -> str | None:
    text = (message.text or "").strip()
    if not text:
        return None

    if message.chat.type == "private":
        return text

    is_reply_to_me = bool(message.reply_to_message and message.reply_to_message.outgoing)

    if text.startswith(GROUP_TRIGGER):
        stripped = text[len(GROUP_TRIGGER):].strip()
        return stripped or None

    if is_reply_to_me:
        return text

    return None


DIRECT_DM_PATTERNS = [
    # /dm @user text  or  /pm @user text
    re.compile(
        r"^\s*/(?:dm|pm)\s+(?P<target>@[A-Za-z0-9_]+|-?\d+)\s+(?P<text>.+?)\s*$",
        re.IGNORECASE,
    ),
    # напиши в личку @user: text  or  отправь в лс @user text
    re.compile(
        r"^\s*(?:напиши|отправь|пошли|скинь)\s+(?:в\s+(?:личку|лс|лк|дм|dm|pm)\s+)?(?P<target>@[A-Za-z0-9_]+|-?\d+)\s*(?:[:,\-]\s*|\s+)(?P<text>.+?)\s*$",
        re.IGNORECASE,
    ),
    # напиши @user привет  (target starts with @, rest is text)
    re.compile(
        r"^\s*(?:напиши|отправь|пошли|скинь)\s+(?P<target>@[A-Za-z0-9_]+)\s+(?P<text>.+?)\s*$",
        re.IGNORECASE,
    ),
]

DIRECT_LINK_PATTERNS = [
    re.compile(
        r"^\s*/(?:sendlink|send_to_link|sl)\s+(?P<link>(?:https?://)?t\.me/\S+|tg://\S+)\s+(?P<text>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:напиши|отправь)\s+(?:сюда|туда|по\s+ссылке|в\s+этот\s+чат|в\s+этот\s+тред|в\s+эту\s+тему)?\s*(?P<link>(?:https?://)?t\.me/\S+|tg://\S+)\s*(?:[:,\-]\s*|\s+)(?P<text>.+?)\s*$",
        re.IGNORECASE,
    ),
]

DIRECT_TOPIC_PATTERNS = [
    re.compile(
        r"^\s*/(?:topic_send|ts)\s+(?:(?P<chat_id>-?\d+)\s+)?(?P<topic_id>\d+)\s+(?:(?P<mention>@[A-Za-z0-9_]+)\s+)?(?P<text>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*/(?:topic_send|ts)\s+(?:(?P<chat_id>-?\d+)\s+)?(?P<topic_ref>.+?)(?:\s+(?P<mention>@[A-Za-z0-9_]+))?\s*:\s*(?P<text>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:напиши|отправь)\s+в\s+(?:тему|топик)\s+(?:(?:чата|группы)\s+(?P<chat_id>-?\d+)\s+)?(?:(?P<topic_id>\d+)\s+(?:(?P<mention>@[A-Za-z0-9_]+)\s*)?(?:[:,\-]\s*|\s+)(?P<text>.+?)|(?P<topic_ref>.+?)(?:\s+(?P<mention_by_title>@[A-Za-z0-9_]+))?\s*:\s*(?P<text_by_title>.+?))\s*$",
        re.IGNORECASE,
    ),
]


def parse_direct_private_message_request(text: str) -> dict[str, str] | None:
    for pattern in DIRECT_DM_PATTERNS:
        match = pattern.match(text.strip())
        if not match:
            continue
        target = match.group("target").strip()
        body = match.group("text").strip()
        if not body:
            return None
        return {"target": target, "text": body}
    return None


def parse_direct_link_request(text: str) -> dict[str, str] | None:
    for pattern in DIRECT_LINK_PATTERNS:
        match = pattern.match(text.strip())
        if not match:
            continue
        link = match.group("link").strip().rstrip(".,:;")
        body = match.group("text").strip()
        if not body:
            return None
        return {"link": link, "text": body}
    return None


def parse_direct_topic_request(text: str) -> dict[str, str | None] | None:
    for pattern in DIRECT_TOPIC_PATTERNS:
        match = pattern.match(text.strip())
        if not match:
            continue

        topic_id = match.groupdict().get("topic_id")
        topic_ref = match.groupdict().get("topic_ref")
        chat_id = match.group("chat_id")
        mention = (
            match.groupdict().get("mention")
            or match.groupdict().get("mention_by_title")
        )
        body = (
            match.groupdict().get("text")
            or match.groupdict().get("text_by_title")
            or ""
        ).strip()
        if not body:
            return None

        return {
            "chat_id": chat_id.strip() if chat_id else None,
            "topic_ref": (
                topic_id.strip()
                if topic_id
                else str(topic_ref).strip().strip("'").strip('"')
            ),
            "mention_username": mention.strip() if mention else None,
            "text": body,
        }
    return None


def topic_message_matches(message: Message, top_message_id: int) -> bool:
    return (
        message.id == top_message_id
        or getattr(message, "reply_to_top_message_id", None) == top_message_id
    )


def topic_participant_lookup_text(entry: dict[str, Any]) -> str:
    sender = entry.get("sender") or {}
    parts = [
        sender.get("id"),
        sender.get("name"),
        sender.get("username"),
        entry.get("last_text"),
    ]
    return " ".join(str(part).lower() for part in parts if part)


def chat_member_lookup_text(entry: dict[str, Any]) -> str:
    parts = [
        entry.get("id"),
        entry.get("name"),
        entry.get("username"),
    ]
    return " ".join(str(part).lower() for part in parts if part)


def serialize_message(m: Message) -> dict[str, Any]:
    text = (m.text or m.caption or "").strip()

    sender = None
    if m.from_user:
        sender = {
            "id": m.from_user.id,
            "name": " ".join(
                x for x in [m.from_user.first_name, m.from_user.last_name] if x
            ).strip() or m.from_user.username or str(m.from_user.id),
            "username": m.from_user.username,
        }
    elif m.sender_chat:
        sender = {
            "id": m.sender_chat.id,
            "name": m.sender_chat.title or str(m.sender_chat.id),
            "username": m.sender_chat.username,
        }

    return {
        "id": m.id,
        "chat_id": m.chat.id,
        "chat_title": m.chat.title or m.chat.first_name,
        "date": m.date.isoformat() if m.date else None,
        "sender": sender,
        "text": text,
        "reply_to_message_id": getattr(m, "reply_to_message_id", None),
        "reply_to_top_message_id": getattr(m, "reply_to_top_message_id", None),
    }


def current_topic_key(message: Message) -> int:
    return getattr(message, "reply_to_top_message_id", None) or 0


def session_key_for(message: Message) -> str:
    base = f"tg:{message.chat.id}:thread:{current_topic_key(message)}"
    suffix = session_reset_ts.get(base, "")
    return f"{base}_{suffix}" if suffix else base


def normalize_target(target: str) -> str | int:
    value = str(target).strip()
    if not value:
        raise ValueError("target is empty")

    if value.startswith("https://t.me/"):
        value = value[len("https://t.me/"):]
    elif value.startswith("http://t.me/"):
        value = value[len("http://t.me/"):]
    elif value.startswith("t.me/"):
        value = value[len("t.me/"):]
    elif value.startswith("tg://resolve?domain="):
        value = value.split("domain=", 1)[1].split("&", 1)[0]

    value = value.strip().strip("/")
    value = value.split("?", 1)[0]
    value = value.split("/", 1)[0]
    if not value:
        raise ValueError("target is empty")

    if value.lstrip("-").isdigit():
        return int(value)

    return value if value.startswith("@") else f"@{value}"


def first_int(values: list[str] | None) -> int | None:
    if not values:
        return None
    raw = str(values[0]).strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return None


def parse_telegram_link(link: str) -> dict[str, Any]:
    value = str(link).strip().rstrip(".,:;")
    if not value:
        raise ValueError("link is empty")

    if value.startswith("t.me/"):
        value = f"https://{value}"

    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    reply_to_message_id = (
        first_int(query.get("thread"))
        or first_int(query.get("topic"))
        or first_int(query.get("comment"))
        or first_int(query.get("reply"))
    )

    if parsed.scheme in {"http", "https"}:
        host = parsed.netloc.lower()
        if host not in {"t.me", "www.t.me"}:
            raise ValueError("unsupported Telegram host")

        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise ValueError("Telegram link has no target")

        if parts[0] in {"joinchat", "addstickers", "addemoji"} or parts[0].startswith("+"):
            raise ValueError("invite links are not supported")

        if parts[0] == "c":
            if len(parts) < 2 or not parts[1].isdigit():
                raise ValueError("invalid private chat link")
            target: str | int = int(f"-100{parts[1]}")
            if len(parts) >= 3 and reply_to_message_id is None and parts[2].isdigit():
                reply_to_message_id = int(parts[2])
        else:
            target = f"@{parts[0]}"
            if len(parts) >= 2 and reply_to_message_id is None and parts[1].isdigit():
                reply_to_message_id = int(parts[1])

        return {
            "target": target,
            "reply_to_message_id": reply_to_message_id,
            "link": value,
        }

    if parsed.scheme == "tg":
        if parsed.netloc != "resolve":
            raise ValueError("unsupported tg:// link")

        domain = (query.get("domain") or [None])[0]
        if not domain:
            raise ValueError("tg:// link has no domain")

        target = f"@{str(domain).lstrip('@')}"
        reply_to_message_id = reply_to_message_id or first_int(query.get("post"))
        return {
            "target": target,
            "reply_to_message_id": reply_to_message_id,
            "link": value,
        }

    raise ValueError("unsupported Telegram link format")


def is_allowed_chat(chat_id: int) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return chat_id in ALLOWED_CHAT_IDS


def chat_label(chat: Any) -> str:
    title = getattr(chat, "title", None)
    first_name = getattr(chat, "first_name", None)
    last_name = getattr(chat, "last_name", None)
    username = getattr(chat, "username", None)

    if title:
        return title
    if first_name or last_name:
        return " ".join(x for x in [first_name, last_name] if x).strip()
    if username:
        return f"@{username}"
    return str(getattr(chat, "id", "unknown"))


def serialize_chat(chat: Any) -> dict[str, Any]:
    username = getattr(chat, "username", None)
    return {
        "chat_id": getattr(chat, "id", None),
        "title": chat_label(chat),
        "username": f"@{username}" if username else None,
        "type": str(getattr(chat, "type", "")),
        "is_forum": bool(getattr(chat, "is_forum", False)),
    }


async def require_forum_chat(chat_id: int) -> Any:
    chat = await app.get_chat(chat_id)
    if not getattr(chat, "is_forum", False):
        raise ValueError(f'chat "{chat_label(chat)}" ({chat.id}) is not a forum chat')
    return chat


def resolve_tool_chat_id(args: dict[str, Any], inbound: Message) -> int:
    raw_chat_id = args.get("chat_id")
    if raw_chat_id is None:
        return inbound.chat.id

    chat_id = normalize_chat_id(raw_chat_id)
    if not is_allowed_chat(chat_id):
        raise ValueError(f"chat {chat_id} is not allowed")
    return chat_id


def resolve_direct_chat_id(chat_id: str | None, inbound: Message) -> int:
    if chat_id is None:
        return inbound.chat.id

    resolved_chat_id = normalize_chat_id(chat_id)
    if not is_allowed_chat(resolved_chat_id):
        raise ValueError(f"chat {resolved_chat_id} is not allowed")
    return resolved_chat_id


def normalize_topic_title(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


async def find_matching_topics(chat_id: int, topic_ref: str) -> list[dict[str, Any]]:
    value = str(topic_ref).strip()
    if not value:
        return []

    if value.isdigit():
        meta = await get_topic_meta(chat_id, int(value))
        return [meta] if meta else []

    topics = (await list_forum_topics(chat_id=chat_id, limit=50, query=value)).get("topics", [])
    if not topics:
        return []

    normalized_value = normalize_topic_title(value)
    exact_matches = [
        topic for topic in topics
        if normalize_topic_title(topic.get("title") or "") == normalized_value
    ]
    return exact_matches or topics


async def resolve_topic_ref(chat_id: int, topic_ref: str) -> dict[str, Any]:
    value = str(topic_ref).strip()
    if not value:
        raise ValueError("topic is empty")

    topics = await find_matching_topics(chat_id, value)
    if not topics:
        raise ValueError(f'topic "{value}" not found')

    normalized_value = normalize_topic_title(value)
    exact_matches = [
        topic for topic in topics
        if normalize_topic_title(topic.get("title") or "") == normalized_value
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    if len(topics) == 1:
        return topics[0]

    titles = ", ".join(
        f'{topic.get("title") or topic["topic_id"]} ({topic["topic_id"]})'
        for topic in topics[:5]
    )
    raise ValueError(
        f'topic "{value}" is ambiguous; matches: {titles}. Use topic_id.'
    )


async def list_searchable_forum_chats(limit: int = 100) -> list[dict[str, Any]]:
    rows = []
    seen_chat_ids: set[int] = set()

    if ALLOWED_CHAT_IDS:
        for chat_id in sorted(ALLOWED_CHAT_IDS):
            chat = await app.get_chat(chat_id)
            if not getattr(chat, "is_forum", False):
                continue
            if chat.id in seen_chat_ids:
                continue
            rows.append({
                "chat_id": chat.id,
                "title": chat_label(chat),
            })
            seen_chat_ids.add(chat.id)
            if len(rows) >= limit:
                break
        return rows

    async for dialog in app.get_dialogs(limit=200):
        chat = dialog.chat
        if not getattr(chat, "is_forum", False):
            continue
        if chat.id in seen_chat_ids:
            continue
        rows.append({
            "chat_id": chat.id,
            "title": chat_label(chat),
        })
        seen_chat_ids.add(chat.id)
        if len(rows) >= limit:
            break

    return rows


async def resolve_direct_topic_target(
    inbound: Message,
    chat_id: str | None,
    topic_ref: str,
) -> dict[str, Any]:
    if chat_id is not None:
        target_chat_id = resolve_direct_chat_id(chat_id, inbound)
        target_topic = await resolve_topic_ref(target_chat_id, topic_ref)
        target_chat = await app.get_chat(target_chat_id)
        return {
            "chat_id": target_chat_id,
            "chat_title": chat_label(target_chat),
            "topic": target_topic,
        }

    if inbound.chat.type != "private":
        try:
            target_topic = await resolve_topic_ref(inbound.chat.id, topic_ref)
            return {
                "chat_id": inbound.chat.id,
                "chat_title": chat_label(inbound.chat),
                "topic": target_topic,
            }
        except ValueError:
            pass

    if MAIN_FORUM_CHAT_ID is not None:
        try:
            target_chat = await require_forum_chat(MAIN_FORUM_CHAT_ID)
            target_topic = await resolve_topic_ref(MAIN_FORUM_CHAT_ID, topic_ref)
            return {
                "chat_id": MAIN_FORUM_CHAT_ID,
                "chat_title": chat_label(target_chat),
                "topic": target_topic,
            }
        except Exception:
            pass

    forum_chats = await list_searchable_forum_chats()
    matches = []
    for forum_chat in forum_chats:
        topics = await find_matching_topics(forum_chat["chat_id"], topic_ref)
        for topic in topics:
            matches.append({
                "chat_id": forum_chat["chat_id"],
                "chat_title": forum_chat["title"],
                "topic": topic,
            })

    if not matches:
        raise ValueError(f'topic "{topic_ref}" not found in available forum chats')

    if len(matches) == 1:
        return matches[0]

    normalized_value = normalize_topic_title(topic_ref)
    exact_matches = [
        match for match in matches
        if normalize_topic_title(match["topic"].get("title") or "") == normalized_value
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    variants = ", ".join(
        f'{match["chat_title"]} -> {match["topic"].get("title") or match["topic"]["topic_id"]} ({match["topic"]["topic_id"]})'
        for match in matches[:5]
    )
    raise ValueError(
        f'topic "{topic_ref}" is ambiguous across chats: {variants}. Add chat_id.'
    )


async def list_available_chats(limit: int = 20, query: str = "") -> dict[str, Any]:
    normalized_query = (query or "").strip().lower()
    rows = []

    if ALLOWED_CHAT_IDS:
        for chat_id in sorted(ALLOWED_CHAT_IDS):
            try:
                chat = await app.get_chat(chat_id)
            except Exception as e:
                rows.append({
                    "chat_id": chat_id,
                    "error": f"{type(e).__name__}: {e}",
                })
                continue

            row = serialize_chat(chat)
            haystack = " ".join(
                str(x).lower()
                for x in [row["chat_id"], row["title"], row["username"], row["type"]]
                if x
            )
            if normalized_query and normalized_query not in haystack:
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return {"chats": rows}

    async for dialog in app.get_dialogs(limit=200):
        chat = dialog.chat
        row = serialize_chat(chat)
        haystack = " ".join(
            str(x).lower()
            for x in [row["chat_id"], row["title"], row["username"], row["type"]]
            if x
        )
        if normalized_query and normalized_query not in haystack:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break

    return {"chats": rows}


async def list_forum_topics(chat_id: int, limit: int = 20, query: str = "") -> dict[str, Any]:
    await require_forum_chat(chat_id)
    peer = await app.resolve_peer(chat_id)
    res = await app.invoke(
        GetForumTopics(
            channel=peer,
            offset_date=0,
            offset_id=0,
            offset_topic=0,
            limit=min(limit, 50),
            q=query or ""
        )
    )

    topics = []
    for t in getattr(res, "topics", []):
        topics.append({
            "topic_id": t.id,
            "title": getattr(t, "title", None),
            "top_message_id": getattr(t, "top_message", None),
        })
    return {"topics": topics}


async def get_topic_meta(chat_id: int, topic_id: int) -> dict[str, Any] | None:
    await require_forum_chat(chat_id)
    peer = await app.resolve_peer(chat_id)
    res = await app.invoke(GetForumTopicsByID(channel=peer, topics=[topic_id]))
    topics = getattr(res, "topics", None) or []
    if not topics:
        return None

    t = topics[0]
    return {
        "topic_id": t.id,
        "title": getattr(t, "title", None),
        "top_message_id": getattr(t, "top_message", None),
    }


async def get_topic_context(chat_id: int, topic_id: int, limit: int = 30) -> dict[str, Any]:
    meta = await get_topic_meta(chat_id, topic_id)
    if not meta:
        return {"error": "topic not found"}

    top_id = meta["top_message_id"]
    rows = []

    try:
        async for m in app.get_discussion_replies(chat_id, top_id, limit=limit):
            rows.append(serialize_message(m))
    except Exception:
        async for m in app.get_chat_history(chat_id, limit=400):
            if topic_message_matches(m, top_id):
                rows.append(serialize_message(m))
            if len(rows) >= limit:
                break

    rows.reverse()
    return {"topic": meta, "messages": rows}


async def get_chat_context(chat_id: int, limit: int = 30) -> dict[str, Any]:
    chat = await app.get_chat(chat_id)
    rows = []

    async for m in app.get_chat_history(chat_id, limit=limit):
        rows.append(serialize_message(m))

    rows.reverse()
    return {
        "chat": serialize_chat(chat),
        "messages": rows,
    }


async def search_messages(
    chat_id: int,
    query: str,
    limit: int = 20,
    from_user: str | None = None,
) -> dict[str, Any]:
    chat = await app.get_chat(chat_id)
    rows = []

    search_kwargs: dict[str, Any] = {"query": query, "limit": limit}
    if from_user:
        from_user_value = str(from_user).strip()
        if from_user_value.lstrip("-").isdigit():
            search_kwargs["from_user"] = int(from_user_value)
        else:
            if not from_user_value.startswith("@"):
                from_user_value = f"@{from_user_value}"
            search_kwargs["from_user"] = from_user_value

    async for m in app.search_messages(chat_id, **search_kwargs):
        rows.append(serialize_message(m))
        if len(rows) >= limit:
            break

    return {
        "chat": serialize_chat(chat),
        "query": query,
        "messages": rows,
    }


async def forward_message(
    from_chat_id: int,
    message_id: int,
    to_chat_id: int,
    to_topic_id: int | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if to_topic_id:
        meta = await get_topic_meta(to_chat_id, to_topic_id)
        if meta:
            kwargs["reply_to_message_id"] = meta["top_message_id"]

    msg = await app.forward_messages(
        chat_id=to_chat_id,
        from_chat_id=from_chat_id,
        message_ids=message_id,
        **kwargs,
    )
    forwarded = msg[0] if isinstance(msg, list) else msg
    return {
        "ok": True,
        "message_id": forwarded.id,
        "from_chat_id": from_chat_id,
        "to_chat_id": to_chat_id,
    }


async def get_user_info(target: str) -> dict[str, Any]:
    normalized = normalize_target(target)
    users = await app.get_users(normalized)
    user = users[0] if isinstance(users, list) else users

    return {
        "id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": f"@{user.username}" if user.username else None,
        "phone": user.phone_number if hasattr(user, "phone_number") else None,
        "bio": getattr(user, "bio", None),
        "is_bot": user.is_bot,
        "status": str(getattr(user, "status", "")),
    }


async def pin_message(chat_id: int, message_id: int, both_sides: bool = True) -> dict[str, Any]:
    await app.pin_chat_message(chat_id, message_id, both_sides=both_sides)
    return {"ok": True, "chat_id": chat_id, "message_id": message_id}


async def send_to_chat(chat_id: int, text: str, reply_to_message_id: int | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id

    msg = await app.send_message(chat_id=chat_id, text=text, **kwargs)
    return {
        "ok": True,
        "message_id": msg.id,
        "chat_id": msg.chat.id,
    }


async def list_topic_participants(
    chat_id: int,
    topic_id: int,
    query: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    meta = await get_topic_meta(chat_id, topic_id)
    if not meta:
        return {"error": "topic not found"}

    top_id = meta["top_message_id"]
    normalized_query = (query or "").strip().lower()
    participants: dict[str, dict[str, Any]] = {}

    messages = []
    try:
        async for m in app.get_discussion_replies(chat_id, top_id, limit=200):
            messages.append(m)
    except Exception:
        async for m in app.get_chat_history(chat_id, limit=400):
            if topic_message_matches(m, top_id):
                messages.append(m)

    for m in messages:
        row = serialize_message(m)
        sender = row.get("sender")
        if not sender:
            continue

        sender_key = str(sender.get("id"))
        if sender_key not in participants:
            participants[sender_key] = {
                "sender": sender,
                "message_count": 0,
                "last_message_id": row["id"],
                "last_message_date": row["date"],
                "last_text": row["text"][:200],
            }

        participants[sender_key]["message_count"] += 1

    rows = list(participants.values())
    if normalized_query:
        rows = [
            entry for entry in rows
            if normalized_query in topic_participant_lookup_text(entry)
        ]

    return {"topic": meta, "participants": rows[:limit]}


async def list_chat_members(
    chat_id: int,
    query: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    normalized_query = (query or "").strip().lower()
    rows = []

    async for member in app.get_chat_members(chat_id):
        user = getattr(member, "user", None)
        if not user:
            continue

        row = {
            "id": user.id,
            "name": " ".join(
                x for x in [user.first_name, user.last_name] if x
            ).strip() or user.username or str(user.id),
            "username": f"@{user.username}" if user.username else None,
            "status": str(getattr(member, "status", "")),
        }

        if normalized_query and normalized_query not in chat_member_lookup_text(row):
            continue

        rows.append(row)
        if len(rows) >= limit:
            break

    return {"chat_id": chat_id, "members": rows}


async def send_to_topic(
    chat_id: int,
    topic_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    mention_username: str | None = None,
) -> dict[str, Any]:
    meta = await get_topic_meta(chat_id, topic_id)
    if not meta:
        return {"error": "topic not found"}

    mention = None
    if mention_username:
        mention_value = str(mention_username).strip()
        if mention_value:
            mention = mention_value if mention_value.startswith("@") else f"@{mention_value}"

    outgoing_text = text
    if mention and mention.lower() not in outgoing_text.lower():
        outgoing_text = f"{mention} {outgoing_text}"

    target_reply_to_message_id = reply_to_message_id or meta["top_message_id"]
    msg = await app.send_message(
        chat_id=chat_id,
        text=outgoing_text,
        reply_to_message_id=target_reply_to_message_id,
    )
    return {
        "ok": True,
        "message_id": msg.id,
        "topic_id": topic_id,
        "reply_to_message_id": target_reply_to_message_id,
        "mention_username": mention,
    }


async def send_private_message(target: str, text: str) -> dict[str, Any]:
    normalized_target = normalize_target(target)
    chat = await app.get_chat(normalized_target)

    msg = await app.send_message(
        chat_id=chat.id,
        text=text,
    )
    return {
        "ok": True,
        "message_id": msg.id,
        "chat_id": msg.chat.id,
        "target": str(normalized_target),
    }


async def send_to_link(link: str, text: str) -> dict[str, Any]:
    parsed_link = parse_telegram_link(link)
    chat = await app.get_chat(parsed_link["target"])

    if chat.type != "private" and not is_allowed_chat(chat.id):
        raise ValueError(f"chat {chat.id} is not allowed")

    kwargs: dict[str, Any] = {}
    if parsed_link["reply_to_message_id"]:
        kwargs["reply_to_message_id"] = parsed_link["reply_to_message_id"]

    msg = await app.send_message(
        chat_id=chat.id,
        text=text,
        **kwargs,
    )
    return {
        "ok": True,
        "message_id": msg.id,
        "chat_id": msg.chat.id,
        "target": parsed_link["target"],
        "reply_to_message_id": parsed_link["reply_to_message_id"],
    }


async def handle_direct_request(message: Message, text: str) -> str | None:
    topic_request = parse_direct_topic_request(text)
    if topic_request:
        target = await resolve_direct_topic_target(
            inbound=message,
            chat_id=topic_request["chat_id"],
            topic_ref=str(topic_request["topic_ref"]),
        )
        target_chat_id = int(target["chat_id"])
        target_topic = target["topic"]
        result = await send_to_topic(
            chat_id=target_chat_id,
            topic_id=int(target_topic["topic_id"]),
            text=str(topic_request["text"]),
            mention_username=(
                str(topic_request["mention_username"])
                if topic_request["mention_username"] is not None
                else None
            ),
        )

        if result.get("mention_username"):
            return (
                f'Отправил в тему "{target_topic.get("title") or result["topic_id"]}" '
                f'в чате "{target["chat_title"]}" '
                f"с пингом {result['mention_username']}."
            )
        return (
            f'Отправил в тему "{target_topic.get("title") or result["topic_id"]}" '
            f'в чате "{target["chat_title"]}".'
        )

    link_request = parse_direct_link_request(text)
    if link_request:
        result = await send_to_link(
            link=link_request["link"],
            text=link_request["text"],
        )
        return f"Отправил по ссылке в чат {result['chat_id']}."

    dm_request = parse_direct_private_message_request(text)
    if not dm_request:
        return None

    result = await send_private_message(
        target=dm_request["target"],
        text=dm_request["text"],
    )
    return f"Отправил в личку {result['target']}."


async def get_recent_context(inbound: Message, limit: int = 12) -> list[dict[str, Any]]:
    rows = []
    topic_top_message_id = current_topic_key(inbound)

    if inbound.chat.type == "private":
        async for m in app.get_chat_history(inbound.chat.id, limit=limit):
            rows.append(serialize_message(m))
        rows.reverse()
        return rows

    if not topic_top_message_id:
        async for m in app.get_chat_history(inbound.chat.id, limit=limit):
            rows.append(serialize_message(m))
        rows.reverse()
        return rows

    try:
        async for m in app.get_discussion_replies(inbound.chat.id, topic_top_message_id, limit=limit):
            rows.append(serialize_message(m))
    except Exception:
        async for m in app.get_chat_history(inbound.chat.id, limit=400):
            if topic_message_matches(m, topic_top_message_id):
                rows.append(serialize_message(m))
            if len(rows) >= limit:
                break

    rows.reverse()
    return rows


async def execute_tool(name: str, args: dict[str, Any], inbound: Message) -> dict[str, Any]:
    chat_id = inbound.chat.id

    is_private = str(getattr(inbound.chat, "type", "")) in {"private", "ChatType.PRIVATE"}
    if not is_private and not is_allowed_chat(chat_id):
        return {"error": f"chat {chat_id} is not allowed"}

    try:
        if name == "list_available_chats":
            return await list_available_chats(
                limit=int(args.get("limit", 20)),
                query=str(args.get("query", "")),
            )

        if name == "list_forum_topics":
            return await list_forum_topics(
                chat_id=resolve_tool_chat_id(args, inbound),
                limit=int(args.get("limit", 20)),
                query=str(args.get("query", "")),
            )

        if name == "get_topic_context":
            return await get_topic_context(
                chat_id=resolve_tool_chat_id(args, inbound),
                topic_id=int(args["topic_id"]),
                limit=int(args.get("limit", 30)),
            )

        if name == "list_topic_participants":
            return await list_topic_participants(
                chat_id=resolve_tool_chat_id(args, inbound),
                topic_id=int(args["topic_id"]),
                query=str(args.get("query", "")),
                limit=int(args.get("limit", 20)),
            )

        if name == "list_chat_members":
            return await list_chat_members(
                chat_id=resolve_tool_chat_id(args, inbound),
                query=str(args.get("query", "")),
                limit=int(args.get("limit", 20)),
            )

        if name == "send_to_topic":
            return await send_to_topic(
                chat_id=resolve_tool_chat_id(args, inbound),
                topic_id=int(args["topic_id"]),
                text=str(args["text"]),
                reply_to_message_id=(
                    int(args["reply_to_message_id"])
                    if args.get("reply_to_message_id") is not None
                    else None
                ),
                mention_username=(
                    str(args["mention_username"])
                    if args.get("mention_username") is not None
                    else None
                ),
            )

        if name == "send_private_message":
            return await send_private_message(
                target=str(args["target"]),
                text=str(args["text"]),
            )

        if name == "send_to_link":
            return await send_to_link(
                link=str(args["link"]),
                text=str(args["text"]),
            )

        if name == "get_chat_context":
            target_chat_id = resolve_tool_chat_id(args, inbound)
            return await get_chat_context(
                chat_id=target_chat_id,
                limit=int(args.get("limit", 30)),
            )

        if name == "send_to_chat":
            target_chat_id = resolve_tool_chat_id(args, inbound)
            return await send_to_chat(
                chat_id=target_chat_id,
                text=str(args["text"]),
                reply_to_message_id=(
                    int(args["reply_to_message_id"])
                    if args.get("reply_to_message_id") is not None
                    else None
                ),
            )

        if name == "search_messages":
            target_chat_id = resolve_tool_chat_id(args, inbound)
            return await search_messages(
                chat_id=target_chat_id,
                query=str(args["query"]),
                limit=int(args.get("limit", 20)),
                from_user=(
                    str(args["from_user"])
                    if args.get("from_user") is not None
                    else None
                ),
            )

        if name == "forward_message":
            from_cid = normalize_chat_id(args["from_chat_id"])
            to_cid = normalize_chat_id(args["to_chat_id"])
            if not is_allowed_chat(from_cid):
                raise ValueError(f"chat {from_cid} is not allowed")
            if not is_allowed_chat(to_cid):
                raise ValueError(f"chat {to_cid} is not allowed")
            return await forward_message(
                from_chat_id=from_cid,
                message_id=int(args["message_id"]),
                to_chat_id=to_cid,
                to_topic_id=(
                    int(args["to_topic_id"])
                    if args.get("to_topic_id") is not None
                    else None
                ),
            )

        if name == "get_user_info":
            return await get_user_info(target=str(args["target"]))

        if name == "pin_message":
            target_chat_id = resolve_tool_chat_id(args, inbound)
            return await pin_message(
                chat_id=target_chat_id,
                message_id=int(args["message_id"]),
                both_sides=bool(args.get("both_sides", True)),
            )

        return {"error": f"unknown tool: {name}"}

    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


CHAT_COMPLETIONS_URL = OPENCLAW_URL.replace("/v1/responses", "/v1/chat/completions")


async def openclaw_post(
    input_payload: Any,
    session_key: str,
    tool_choice: str = "auto",
) -> dict[str, Any]:
    """Send request using Chat Completions API format (/v1/chat/completions)."""
    headers = {
        "Authorization": f"Bearer {OPENCLAW_TOKEN}",
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_key,
    }

    # Build messages array for Chat Completions format
    if isinstance(input_payload, str):
        # Initial user message
        messages = [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": input_payload},
        ]
    elif isinstance(input_payload, list):
        # Tool call outputs — rebuild conversation from stored history
        messages = input_payload  # will be pre-built in run_agent
    else:
        messages = [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False)},
        ]

    payload = {
        "model": f"openclaw:{OPENCLAW_AGENT_ID}",
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": tool_choice,
        "max_tokens": 2048,
    }

    tool_names = [t["function"]["name"] for t in TOOLS if "function" in t]
    log.info("OpenClaw request — url=%s, session=%s, tools=%d, tool_choice=%s",
             CHAT_COMPLETIONS_URL, session_key, len(tool_names), tool_choice)
    if isinstance(input_payload, str):
        log.info("OpenClaw input (first 500 chars): %s", input_payload[:500])

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(CHAT_COMPLETIONS_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    # Debug: log response
    choices = data.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        finish = choices[0].get("finish_reason", "?")
        tool_calls = msg.get("tool_calls", [])
        content = (msg.get("content") or "")[:200]
        log.info("OpenClaw response — finish=%s, tool_calls=%d, content=%s",
                 finish, len(tool_calls), content)
        for tc in tool_calls:
            fn = tc.get("function", {})
            log.info("  tool_call: %s(%s)", fn.get("name"), str(fn.get("arguments", ""))[:200])
    else:
        log.warning("OpenClaw response — no choices! raw=%s", json.dumps(data)[:500])

    return data


def extract_function_calls(resp: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool calls from Chat Completions response."""
    calls = []

    choices = resp.get("choices", [])
    if not choices:
        return calls

    msg = choices[0].get("message", {})
    for tc in msg.get("tool_calls", []):
        fn = tc.get("function", {})
        raw_args = fn.get("arguments") or "{}"
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}

        calls.append({
            "call_id": tc.get("id"),
            "name": fn.get("name"),
            "arguments": args,
        })

    return calls


def extract_text(resp: dict[str, Any]) -> str:
    """Extract text from Chat Completions response."""
    choices = resp.get("choices", [])
    if not choices:
        return "Не смог получить ответ от OpenClaw."

    msg = choices[0].get("message", {})
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    return "Не смог получить текстовый ответ от OpenClaw."


async def get_available_chats_summary(limit: int = 30) -> list[dict[str, Any]]:
    rows = []

    if ALLOWED_CHAT_IDS:
        for chat_id in sorted(ALLOWED_CHAT_IDS):
            try:
                chat = await app.get_chat(chat_id)
                rows.append(serialize_chat(chat))
            except Exception:
                rows.append({"chat_id": chat_id, "error": "cannot access"})
            if len(rows) >= limit:
                break
        return rows

    async for dialog in app.get_dialogs(limit=200):
        chat = dialog.chat
        chat_type = str(getattr(chat, "type", ""))
        if chat_type in {"bot", "ChatType.BOT"}:
            continue
        rows.append(serialize_chat(chat))
        if len(rows) >= limit:
            break

    return rows


_ACTION_KEYWORDS = re.compile(
    r"напиши|отправь|пошли|скинь|перешли|закрепи|найди|покажи|прочитай|"
    r"send|write|forward|pin|search|read|list|get|show|"
    r"посмотри|проверь|узнай|спроси|скажи\s",
    re.IGNORECASE,
)


def _looks_like_action(text: str) -> bool:
    return bool(_ACTION_KEYWORDS.search(text))


async def run_agent(inbound: Message, user_text: str) -> str:
    recent_context = await get_recent_context(inbound)
    available_chats = await get_available_chats_summary()

    tool_names = [t["function"]["name"] for t in TOOLS if "function" in t]

    context_json = json.dumps(
        {
            "chat": {
                "id": inbound.chat.id,
                "title": inbound.chat.title or inbound.chat.first_name,
                "type": str(inbound.chat.type),
            },
            "thread": {
                "top_message_id": current_topic_key(inbound) or None,
                "is_topic_message": bool(current_topic_key(inbound)),
            },
            "incoming_message": serialize_message(inbound),
            "recent_context": recent_context,
            "available_chats": available_chats,
        },
        ensure_ascii=False,
        indent=2,
    )

    user_content = (
        f"Контекст Telegram:\n{context_json}\n\n"
        f"Доступные tools: {', '.join(tool_names)}\n\n"
        f"Запрос пользователя: {user_text}"
    )

    # Build messages for Chat Completions
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": INSTRUCTIONS},
        {"role": "user", "content": user_content},
    ]

    session_key = session_key_for(inbound)

    first_tool_choice = "required" if _looks_like_action(user_text) else "auto"
    log.info("run_agent — user_text=%r, first_tool_choice=%s", user_text[:100], first_tool_choice)
    resp = await openclaw_post(messages, session_key, tool_choice=first_tool_choice)

    for _ in range(MAX_TOOL_CALLS):
        calls = extract_function_calls(resp)
        if not calls:
            return extract_text(resp)

        # Add assistant message with tool_calls to conversation history
        assistant_msg = resp.get("choices", [{}])[0].get("message", {})
        messages.append(assistant_msg)

        # Execute tools and add results to conversation
        for call in calls:
            result = await execute_tool(call["name"], call["arguments"], inbound)
            messages.append({
                "role": "tool",
                "tool_call_id": call["call_id"],
                "content": json.dumps(result, ensure_ascii=False),
            })

        # Continue with full conversation history
        resp = await openclaw_post(messages, session_key, tool_choice="auto")

    return "Остановлено: слишком много циклов tools."


async def send_chunks(message: Message, text: str) -> None:
    parts = [text[i:i + 3500] for i in range(0, len(text), 3500)] or ["(пустой ответ)"]
    topic_reply_to_message_id = current_topic_key(message) or None

    first = True
    for part in parts:
        if first:
            await message.reply_text(part)
            first = False
        else:
            kwargs: dict[str, Any] = {}
            if topic_reply_to_message_id:
                kwargs["reply_to_message_id"] = topic_reply_to_message_id
            await app.send_message(message.chat.id, part, **kwargs)


@app.on_message(filters.text)
async def handle_text(_: Client, message: Message):
    if message.outgoing:
        return

    if not is_allowed_chat(message.chat.id):
        return

    text = normalize_input(message)
    if not text:
        return

    # Reset session command
    if text.strip() == "!reset":
        import time
        base_key = f"tg:{message.chat.id}:thread:{current_topic_key(message)}"
        new_suffix = str(int(time.time()))
        session_reset_ts[base_key] = new_suffix
        new_key = session_key_for(message)
        await message.reply_text(f"Сессия сброшена.\nНовый ключ: {new_key}")
        return

    # List tools command
    if text.strip() == "!tools":
        lines = [f"Всего tools: {len(TOOLS)}\n"]
        for t in TOOLS:
            fn = t.get("function", {})
            name = fn.get("name", "?")
            params = fn.get("parameters", {}).get("required", [])
            lines.append(f"• {name}({', '.join(params)})")
        await send_chunks(message, "\n".join(lines))
        return

    # Debug command: test Chat Completions with tool_choice=auto
    if text.strip() == "!debug":
        try:
            headers = {
                "Authorization": f"Bearer {OPENCLAW_TOKEN}",
                "Content-Type": "application/json",
                "x-openclaw-session-key": session_key_for(message) + "_debug",
            }
            payload = {
                "model": f"openclaw:{OPENCLAW_AGENT_ID}",
                "messages": [
                    {"role": "system", "content": INSTRUCTIONS},
                    {"role": "user", "content": "Вызови list_available_chats чтобы показать доступные чаты."},
                ],
                "tools": TOOLS,
                "tool_choice": "auto",
                "max_tokens": 2048,
            }
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(CHAT_COMPLETIONS_URL, headers=headers, json=payload)
                raw = r.text
            debug_out = f"Endpoint: {CHAT_COMPLETIONS_URL}\nStatus: {r.status_code}\n\n"
            debug_out += f"Tools sent: {len(TOOLS)}\n\n"
            debug_out += f"Response (first 3000 chars):\n{raw[:3000]}"
            await send_chunks(message, debug_out)
        except Exception as e:
            await message.reply_text(f"Debug error: {e}")
        return

    # Debug2: force tool_choice=required
    if text.strip() == "!debug2":
        try:
            headers = {
                "Authorization": f"Bearer {OPENCLAW_TOKEN}",
                "Content-Type": "application/json",
                "x-openclaw-session-key": session_key_for(message) + "_debug2",
            }
            payload = {
                "model": f"openclaw:{OPENCLAW_AGENT_ID}",
                "messages": [
                    {"role": "system", "content": "Ты assistant. Используй tools."},
                    {"role": "user", "content": "Покажи список чатов."},
                ],
                "tools": TOOLS,
                "tool_choice": "required",
                "max_tokens": 2048,
            }
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(CHAT_COMPLETIONS_URL, headers=headers, json=payload)
                raw = r.text
            debug_out = f"Endpoint: {CHAT_COMPLETIONS_URL}\nStatus: {r.status_code}\ntool_choice: required\n\n"
            debug_out += f"Response (first 3000 chars):\n{raw[:3000]}"
            await send_chunks(message, debug_out)
        except Exception as e:
            await message.reply_text(f"Debug2 error: {e}")
        return

    # Debug3: try old Responses API for comparison
    if text.strip() == "!debug3":
        try:
            headers = {
                "Authorization": f"Bearer {OPENCLAW_TOKEN}",
                "Content-Type": "application/json",
                "x-openclaw-session-key": session_key_for(message) + "_debug3",
            }
            payload = {
                "model": f"openclaw:{OPENCLAW_AGENT_ID}",
                "instructions": INSTRUCTIONS,
                "input": "Вызови list_available_chats.",
                "tools": TOOLS,
                "tool_choice": "required",
                "max_output_tokens": 2048,
            }
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(OPENCLAW_URL, headers=headers, json=payload)
                raw = r.text
            debug_out = f"Endpoint: {OPENCLAW_URL} (Responses API)\nStatus: {r.status_code}\ntool_choice: required\n\n"
            debug_out += f"Response (first 3000 chars):\n{raw[:3000]}"
            await send_chunks(message, debug_out)
        except Exception as e:
            await message.reply_text(f"Debug3 error: {e}")
        return

    lock = chat_locks[session_key_for(message)]
    async with lock:
        try:
            direct_reply = await handle_direct_request(message, text)
            if direct_reply:
                await send_chunks(message, direct_reply)
                return

            reply = await run_agent(message, text)
        except httpx.HTTPStatusError as e:
            body = e.response.text[:1000]
            await message.reply_text(
                f"OpenClaw вернул HTTP {e.response.status_code}:\n{body}"
            )
            return
        except Exception as e:
            await message.reply_text(f"Ошибка bridge: {type(e).__name__}: {e}")
            return

        await send_chunks(message, reply)


if __name__ == "__main__":
    try:
        app.run()
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e).lower():
            session_file = f"{SESSION_NAME}.session"
            raise SystemExit(
                "Pyrogram session database is locked.\n"
                f"Session file: {session_file}\n"
                "Most likely another process is already using the same Telegram session.\n"
                "Stop the old process or set a different PYROGRAM_SESSION value."
            ) from e
        raise
