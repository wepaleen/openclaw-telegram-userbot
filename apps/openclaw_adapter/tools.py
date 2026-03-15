"""Typed tool schemas exposed to OpenClaw for the Telegram manager runtime."""

from typing import Any


def build_default_tool_schemas() -> list[dict[str, Any]]:
    """Return the minimal v1 tool surface for the OpenClaw agent runtime."""
    return [
        {
            "type": "function",
            "function": {
                "name": "resolve_recipient",
                "description": "Разрешить человека, @username или алиас в конкретный peer Telegram.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "prefer_current_chat": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "resolve_target_context",
                "description": "Определить конкретный Telegram-контекст для отправки: чат, reply chain или topic context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chat_query": {"type": "string"},
                        "topic_query": {"type": "string"},
                        "reply_to_message_id": {"type": "integer"},
                        "prefer_current_context": {"type": "boolean"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "parse_time",
                "description": "Преобразовать человеческое описание времени в локальное и UTC время с confidence.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "time_phrase": {"type": "string"},
                        "timezone": {"type": "string"},
                    },
                    "required": ["time_phrase"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_task",
                "description": "Создать задачу в task core.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "assignee_query": {"type": "string"},
                        "due_phrase": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_task",
                "description": "Обновить поля задачи по id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer"},
                        "status": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "assignee_query": {"type": "string"},
                        "due_phrase": {"type": "string"},
                    },
                    "required": ["task_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_tasks",
                "description": "Показать задачи по фильтру.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "assignee_query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "complete_task",
                "description": "Пометить задачу выполненной.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer"},
                    },
                    "required": ["task_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_reminder",
                "description": "Создать напоминание в task core.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "time_phrase": {"type": "string"},
                        "target_query": {"type": "string"},
                    },
                    "required": ["text", "time_phrase"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancel_reminder",
                "description": "Отменить напоминание по id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reminder_id": {"type": "integer"},
                    },
                    "required": ["reminder_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_overdue_tasks",
                "description": "Показать просроченные задачи.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_messages",
                "description": "Искать сообщения в Telegram.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "chat_query": {"type": "string"},
                        "from_query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_recent_context",
                "description": "Получить недавний контекст текущего или указанного Telegram-контекста.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chat_query": {"type": "string"},
                        "topic_query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_message",
                "description": "Отправить сообщение в Telegram в разрешённый peer/context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_query": {"type": "string"},
                        "chat_query": {"type": "string"},
                        "topic_query": {"type": "string"},
                        "text": {"type": "string"},
                        "reply_to_message_id": {"type": "integer"},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_available_chats",
                "description": "Показать доступные Telegram-диалоги и чаты для текущего userbot аккаунта.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
    ]
