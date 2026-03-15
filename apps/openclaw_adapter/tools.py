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
                "description": "Создать напоминание. По умолчанию — в текущем контексте. С chat_query/topic_query — в другом чате/топике. С target_query — тегнуть конкретного человека при срабатывании (@username, имя). recurrence — для повторяющихся (например 'каждый день', 'every 2 hours').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Текст напоминания"},
                        "time_phrase": {"type": "string", "description": "Когда напомнить: 'через 30 мин', 'завтра в 10:00', '3 июня', '14:30'"},
                        "chat_query": {"type": "string", "description": "Чат для доставки (название, @username, id). Если не указан — текущий чат"},
                        "topic_query": {"type": "string", "description": "Топик форума для доставки (название или id)"},
                        "target_query": {"type": "string", "description": "Кого тегнуть при срабатывании (@username, имя контакта)"},
                        "recurrence": {"type": "string", "description": "Повторение: 'каждый день', 'every 2 hours', 'еженедельно'"},
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
                "description": "Показать напоминания. Если status не передан, верни полную картину по pending, fired и cancelled.",
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
                "name": "inspect_delayed_items",
                "description": "Показать полную картину по отложенным сущностям: reminders, scheduled actions и последние записи доставки/исполнения в audit log.",
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
                "name": "list_audit_log",
                "description": "Показать последние записи audit log, чтобы проверить, что реально было выполнено системой.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action_type": {"type": "string"},
                        "success": {"type": "boolean"},
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
                "description": "Переслать сообщение в другой Telegram-чат, личку, контакт или тему. target_query может быть контактом, названием диалога, @username или user id. Если message_id не передан, используется сообщение, на которое ответили.",
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
                "description": "Отправить личное сообщение человеку по контакту из contact book, названию диалога, @username или user id.",
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
                "description": "Отправить сообщение в Telegram. Без параметров — в текущий чат/топик. С chat_query — в другой чат. С topic_query — в конкретный топик форума. С target_query — конкретному адресату (@username, имя контакта, user id).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_query": {"type": "string", "description": "Адресат: @username, имя контакта или user id"},
                        "chat_query": {"type": "string", "description": "Название чата для отправки в другой чат"},
                        "topic_query": {"type": "string", "description": "Название топика форума"},
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
                "name": "edit_message",
                "description": "Редактировать текст существующего сообщения. message_id обязателен. Если chat_query не указан — текущий чат.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "integer", "description": "ID сообщения для редактирования"},
                        "text": {"type": "string", "description": "Новый текст сообщения"},
                        "chat_query": {"type": "string", "description": "Чат (название, @username, id). Если не указан — текущий"},
                    },
                    "required": ["message_id", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_message",
                "description": "Удалить одно или несколько сообщений. message_ids — список ID. revoke=true удалит для всех.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Список ID сообщений для удаления",
                        },
                        "chat_query": {"type": "string", "description": "Чат (название, @username, id). Если не указан — текущий"},
                        "revoke": {"type": "boolean", "description": "Удалить для всех (true) или только для себя (false). По умолчанию true"},
                    },
                    "required": ["message_ids"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_reaction",
                "description": "Поставить реакцию-эмодзи на сообщение. Без message_id — на сообщение, на которое ответили. emoticon по умолчанию 👍.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "integer", "description": "ID сообщения. Если не указан — reply_to из текущего контекста"},
                        "emoticon": {"type": "string", "description": "Эмодзи реакции: 👍, ❤️, 🔥, 👀, 😂, 😢, 🤔 и т.д."},
                        "chat_query": {"type": "string", "description": "Чат (название, @username, id). Если не указан — текущий"},
                    },
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
