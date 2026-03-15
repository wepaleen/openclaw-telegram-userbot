"""Typed tool schemas exposed to OpenClaw for the Telegram manager runtime."""

from typing import Any


def build_default_tool_schemas() -> list[dict[str, Any]]:
    """Return the minimal v1 tool surface for the OpenClaw agent runtime."""
    return [
        {
            "type": "function",
            "function": {
                "name": "list_contacts",
                "description": "Показать контакты из локальной контактной книги, чтобы выбрать адресата для личного сообщения.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
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
                "description": "Создать одноразовое или повторяющееся напоминание в task core.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "time_phrase": {"type": "string"},
                        "target_query": {"type": "string"},
                        "recurrence": {"type": "string"},
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
                "name": "list_reminders",
                "description": "Показать активные или завершённые напоминания.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "schedule_action",
                "description": "Запланировать отложенную отправку сообщения или повторный запуск агента в Telegram на указанное время. Для сложных сценариев укажи action_type=run_agent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "time_phrase": {"type": "string"},
                        "text": {"type": "string"},
                        "target_query": {"type": "string"},
                        "chat_query": {"type": "string"},
                        "topic_query": {"type": "string"},
                        "reply_to_message_id": {"type": "integer"},
                        "action_type": {"type": "string"},
                    },
                    "required": ["time_phrase", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_scheduled_actions",
                "description": "Показать отложенные действия и повторные AI-запуски по фильтру статуса.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancel_scheduled_action",
                "description": "Отменить отложенное действие по id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scheduled_id": {"type": "integer"},
                    },
                    "required": ["scheduled_id"],
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
                "name": "list_chat_members",
                "description": "Показать участников текущего или указанного Telegram-чата.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chat_query": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_topic_participants",
                "description": "Показать участников текущей или указанной темы форума и их последние сообщения.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chat_query": {"type": "string"},
                        "topic_query": {"type": "string"},
                        "query": {"type": "string"},
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
                "name": "forward_message",
                "description": "Переслать сообщение в другой Telegram-чат, личку или тему. Если message_id не передан, используется сообщение, на которое ответили.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "integer"},
                        "from_chat_query": {"type": "string"},
                        "target_query": {"type": "string"},
                        "chat_query": {"type": "string"},
                        "topic_query": {"type": "string"},
                        "reply_to_message_id": {"type": "integer"},
                        "drop_author": {"type": "boolean"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pin_message",
                "description": "Закрепить сообщение в текущем или указанном Telegram-чате. Если message_id не передан, используется сообщение, на которое ответили.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "integer"},
                        "chat_query": {"type": "string"},
                        "notify": {"type": "boolean"},
                    },
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
                "name": "send_private_message",
                "description": "Отправить личное сообщение человеку по контакту, @username или user id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_query": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["target_query", "text"],
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
