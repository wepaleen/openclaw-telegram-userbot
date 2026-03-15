"""Prompt templates for LLM interactions."""

INTENT_CLASSIFY = """Ты классификатор намерений Telegram-бота.
Определи намерение пользователя и извлеки параметры.

Доступные намерения:
- send_private: отправить личное сообщение (params: target, text)
- send_chat: отправить в групповой чат (params: chat_name, text)
- send_topic: отправить в тему форума (params: topic_name, chat_name, text, mention_username)
- send_link: отправить по ссылке (params: link, text)
- search_messages: найти сообщения (params: query, chat_name, from_user)
- forward_message: переслать сообщение (params: description)
- pin_message: закрепить сообщение (params: description)
- summarize: подвести итоги/резюме чата или темы (params: chat_name, topic_name, period)
- create_reminder: напоминание (params: text, when)
- create_task: создать задачу (params: title, description, due, assignee)
- list_tasks: показать задачи (params: status, assignee)
- list_reminders: показать напоминания (params: status)
- get_chat_context: прочитать чат (params: chat_name, limit)
- get_topic_context: прочитать тему (params: topic_name, chat_name, limit)
- list_chats: показать доступные чаты (params: query)
- list_topics: показать темы форума (params: chat_name)
- get_user_info: информация о пользователе (params: target)
- add_contact: добавить контакт (params: name, target)
- respond_text: просто ответить текстом, без действий (params: text)

Контакты пользователя:
{contacts}

Доступные чаты:
{chats}

ВАЖНО: ответь СТРОГО одним JSON объектом:
{{"intent": "...", "confidence": 0.0-1.0, "params": {{...}}}}
Без пояснений, без markdown."""

ENTITY_RESOLVE = """Разреши неоднозначные сущности.

Контакты: {contacts}
Чаты: {chats}

Запрос: "{user_text}"
Намерение: {intent}
Сущности для разрешения: {unresolved}

Ответь СТРОГО одним JSON:
{{"resolved": {{"entity_name": "resolved_value"}}, "ambiguous": ["entity_needing_clarification"]}}"""

COMPLEX_PLAN = """Ты планировщик действий Telegram-бота.
Разбей сложный запрос на последовательность простых действий.

Доступные действия:
- search_messages(chat_id, query, from_user, limit)
- forward_message(from_chat_id, message_id, to_chat_id)
- send_private(target, text)
- send_chat(chat_id, text)
- send_topic(chat_id, topic_id, text, mention_username)
- get_chat_context(chat_id, limit)
- get_topic_context(chat_id, topic_id, limit)
- summarize(chat_id, topic_id, limit)
- respond_text(text)

Контексты: {context}

Ответь СТРОГО JSON:
{{"steps": [{{"action": "...", "params": {{...}}, "depends_on": null}}]}}
Максимум 6 шагов."""

TEXT_GENERATE = """Сгенерируй текст на основе данных.

{instruction}

Данные:
{data}"""

SUMMARIZE_MESSAGES = """Подведи краткие итоги переписки ниже.
Выдели ключевые моменты, решения, вопросы.
Ответь по-русски, коротко и по делу.

Переписка:
{messages}"""

COMPOSE_REPLY = """Помоги сформулировать ответ.

Контекст переписки:
{context}

Задача: {task}

Ответь только текстом сообщения, без пояснений."""
