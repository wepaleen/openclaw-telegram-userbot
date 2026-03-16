# Telegram Userbot Assistant

Персональный AI-ассистент на базе Telegram userbot (MTProto). Умеет управлять задачами, напоминаниями, отправлять сообщения, пересылать, закреплять, искать — всё через естественный язык в чате.

## Архитектура

```
┌──────────────────────────────────────────────────────────────┐
│                     Telegram MTProto                         │
│                   (Telethon userbot)                         │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
               ▼                               ▼
┌──────────────────────────┐    ┌──────────────────────────────┐
│   telethon_bridge/       │    │   telethon_manager_runtime   │
│   • client.py            │    │   • Связывает transport,     │
│   • index_sync.py        │    │     LLM и scheduler          │
│   • serializers.py       │    │   • Обработка входящих       │
│                          │    │     событий                   │
│   Transport layer:       │    │   • Выполнение scheduled     │
│   отправка, пересылка,   │    │     действий                  │
│   закрепление, поиск     │    │                              │
└──────────────────────────┘    └───────────────┬──────────────┘
                                                │
                                    ┌───────────▼───────────┐
                                    │    Роутер (runtime.py) │
                                    │                       │
                                    │  _looks_like_action()  │
                                    │  определяет тип        │
                                    │  запроса               │
                                    └───┬───────────┬───────┘
                                        │           │
                          Действие      │           │  Разговор
                     (напомни, отправь,  │           │  (привет,
                      задача, найди)     │           │   что думаешь?)
                                        │           │
                                        ▼           ▼
                              ┌──────────────┐ ┌──────────────────┐
                              │  OpenRouter   │ │    OpenClaw      │
                              │  Grok 4.1    │ │    GPT-5.4       │
                              │  Fast        │ │    (Codex)        │
                              │  ПЛАТНЫЙ     │ │                  │
                              │  + tools     │ │  БЕСПЛАТНЫЙ      │
                              │  + function  │ │  - tools          │
                              │    calling   │ │  + разговоры      │
                              └──────┬───────┘ └──────────────────┘
                                     │
                                     ▼
                           ┌──────────────────┐
                           │  Tool Executor    │
                           │  26 инструментов  │
                           │                  │
                           │  → SQLite (tasks, │
                           │    reminders)     │
                           │  → Telegram API   │
                           │    (send, forward,│
                           │    pin, search)   │
                           └──────┬───────────┘
                                  │
                                  ▼
              ┌────────────────────────────────────────┐
              │            SQLite (bot.db)              │
              │                                        │
              │  contacts · chat_index · topic_index   │
              │  tasks · reminders · scheduled_actions │
              │  audit_log                             │
              └────────────────────────────────────────┘
                                  ▲
                                  │ каждые 30 сек
                           ┌──────┴───────────┐
                           │    Scheduler      │
                           │                  │
                           │  • напоминания    │
                           │  • дедлайны       │
                           │  • scheduled      │
                           │    actions        │
                           │  • рекуррентные   │
                           │    напоминания    │
                           └──────────────────┘
```

## Dual-LLM: экономия токенов

Бот использует **две LLM** одновременно:

| | OpenRouter (Grok 4.1 Fast) | OpenClaw (GPT-5.4 Codex) |
|---|---|---|
| **Стоимость** | Платный ($0.20/$0.50 за 1M токенов) | Бесплатный |
| **Tool calling** | Да (нативный OpenAI формат) | Нет |
| **Контекст** | 2M токенов | — |
| **Когда используется** | Действия: напомни, отправь, создай задачу, найди, перешли | Разговоры: привет, что думаешь, объясни, расскажи |
| **Роутинг** | Ключевые слова в сообщении | Всё остальное |

Роутер определяет тип запроса по ключевым словам (`напиши`, `отправь`, `напомни`, `задач`, `найди`, `send`, `remind`, `schedule` и т.д.). Если найдено — идёт в платную модель с tools. Если нет — в бесплатный OpenClaw. Модель настраивается через `LLM_MODEL` в `.env` (см. [docs/llm-model-comparison.md](docs/llm-model-comparison.md)).

## 32 инструмента (tools)

### Контакты и чаты
- `list_contacts` — список контактов из БД
- `add_contact` — добавить/обновить контакт в книге
- `resolve_recipient` — найти пользователя/чат по имени, username, ID
- `resolve_target_context` — получить контекст чата (peer, topic)
- `list_available_chats` — доступные чаты и группы
- `list_chat_members` — участники чата
- `list_topic_participants` — участники темы в форуме

### Сообщения
- `send_message` — отправить в текущий чат/тему
- `send_private_message` — отправить личное сообщение
- `forward_message` — переслать сообщение
- `pin_message` — закрепить сообщение
- `edit_message` — редактировать сообщение
- `delete_message` — удалить сообщение
- `send_reaction` — поставить реакцию
- `search_messages` — поиск сообщений в чате
- `get_recent_context` — последние сообщения из чата

### Задачи
- `create_task` — создать задачу с дедлайном
- `update_task` — обновить задачу
- `complete_task` — завершить задачу
- `list_tasks` — список задач
- `list_overdue_tasks` — просроченные задачи

### Напоминания
- `set_reminder` — создать напоминание (разовое или рекуррентное)
- `cancel_reminder` — отменить напоминание
- `list_reminders` — список активных напоминаний
- `inspect_delayed_items` — проверить отложенные items

### Планирование
- `schedule_action` — запланировать действие (отправка, run_agent)
- `list_scheduled_actions` — список запланированных действий
- `cancel_scheduled_action` — отменить действие

### Google Sheets
- `read_spreadsheet` — чтение данных из Google Таблиц
- `list_sheets` — список листов в таблице

### Утилиты
- `parse_time` — разобрать временную фразу ("через 5 минут", "завтра в 10:00")
- `list_audit_log` — журнал аудита

## Scheduler

Фоновый процесс, тикает каждые 30 секунд:

1. **Напоминания** — проверяет `fire_at`, отправляет в ЛС (если персональное) или в чат/тему (если групповое)
2. **Дедлайны задач** — уведомляет если задача просрочена
3. **Scheduled actions** — выполняет запланированные действия:
   - `send_message` / `send_private` / `send_chat` / `send_topic` — отправка сообщения
   - `run_agent` — запуск полного AI-агент цикла (создаёт синтетическое событие и прогоняет через LLM + tools)
4. **Рекуррентные** — после срабатывания вычисляет следующий `fire_at` и создаёт новую запись

## Установка

### Требования
- Python 3.11+
- Telegram API credentials (`API_ID`, `API_HASH`)
- OpenClaw Gateway (бесплатный, для разговоров)
- OpenRouter API key (платный, для tool calling)

### Установка зависимостей

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Создание Telethon сессии

```bash
python3 scripts/create_telethon_session.py
```

Скрипт попросит номер телефона, код из Telegram и 2FA пароль (если есть). Выдаст строку `TELETHON_STRING_SESSION` — скопировать в `.env`.

## Настройка `.env`

```env
# === Telegram ===
API_ID=123456
API_HASH=your_api_hash
TELETHON_STRING_SESSION=your_session_string
TELETHON_SESSION_NAME=openclaw_userbot_telethon

# === LLM: Tool-calling (платный) ===
LLM_BASE_URL=https://openrouter.ai/api/v1/chat/completions
LLM_API_KEY=sk-or-v1-your-openrouter-key
LLM_MODEL=x-ai/grok-4.1-fast

# === LLM: Разговоры (бесплатный) ===
OPENCLAW_URL=http://127.0.0.1:18789/v1/chat/completions
OPENCLAW_TOKEN=your_gateway_token
OPENCLAW_MODEL=openclaw:main

# === Поведение ===
GROUP_TRIGGER=!ai
MAX_TOOL_CALLS=12
BOT_RUNTIME=telethon

# === БД ===
BOT_DB_PATH=bot.db
BOT_TIMEZONE=Europe/Moscow
```

Если `OPENCLAW_URL` не указан — все запросы идут через OpenRouter (платно, но с tools).

## Запуск

```bash
source .venv/bin/activate
python3 -m main_telethon
```

### На сервере (systemd / tmux)

```bash
# tmux
tmux new -s bot
cd /home/ubuntu/openclaw-telegram-userbot
source .venv/bin/activate
python3 -m main_telethon

# OpenClaw gateway (отдельная сессия)
tmux new -s openclaw
openclaw gateway --port 18789
```

## Структура проекта

```
apps/
  telethon_bridge/        # Telegram transport (Telethon MTProto)
    client.py             #   отправка, пересылка, закрепление, поиск
    index_sync.py         #   синхронизация диалогов и топиков в SQLite
    serializers.py        #   сериализация Telethon объектов
  openclaw_adapter/       # LLM адаптер
    client.py             #   HTTP клиент к OpenAI-совместимому API
    runtime.py            #   агентный цикл + dual-LLM роутер
    tools.py              #   32 tool-схем для LLM
    tool_executor.py      #   выполнение tools (dispatch → store/transport)
    instructions.py       #   системный промпт + правила конфиденциальности
  task_core/              # Задачи, напоминания, планирование
    scheduler.py          #   фоновый scheduler (30s tick)
    store/task_store.py   #   CRUD для tasks, reminders, scheduled_actions
    db.py                 #   SQLite init + миграции
    audit.py              #   аудит-лог
  telethon_manager_runtime.py  # Главный оркестратор (связывает всё)
shared/
  schemas/telegram.py     # PeerRef, InboundTelegramEvent, OutboundTelegramCommand
resolver/
  contacts.py             # Контактная книга (поиск, fuzzy match)
  chats.py                # Индекс чатов и топиков (Cyrillic-safe)
config.py                 # Settings (из .env)
docs/
  llm-model-comparison.md # Сравнение LLM-моделей для tool-calling
migrations/
  001_initial.sql         # Схема БД
  002_task_deadline_notifications.sql
```

## База данных (SQLite)

| Таблица | Назначение |
|---|---|
| `contacts` | Контакты пользователя |
| `chat_index` | Индекс доступных чатов (синхронизируется при старте) |
| `topic_index` | Индекс топиков в forum-группах |
| `tasks` | Задачи с дедлайнами |
| `reminders` | Напоминания (разовые и рекуррентные) |
| `scheduled_actions` | Запланированные действия |
| `audit_log` | Лог всех tool-вызовов |

## Примеры использования

### Напоминания
```
напомни через 5 минут позвонить маме
напомни завтра в 10:00 отправить отчёт
напомни каждый день в 9:00 проверить почту
```

### Задачи
```
создай задачу "подготовить презентацию" дедлайн пятница
покажи мои задачи
отметь задачу 3 выполненной
```

### Сообщения
```
напиши Герычу "привет, как дела?"
перешли последнее сообщение в чат "Работа"
закрепи сообщение 42
найди сообщения про "отчёт" в этом чате
```

### Планирование
```
запланируй отправку "доброе утро" в 8:00 завтра
запланируй run_agent "проверь дедлайны" каждый день в 18:00
```

## Безопасность и конфиденциальность

Бот разделяет собеседников на три типа:

| Тип | Определение | Доступ |
|-----|------------|--------|
| **Команда** | В контактной книге (`contacts` в БД) | Полный доступ к внутренней информации |
| **Клиенты** | Не в контактах, но в общем чате с командой | Только по своему проекту |
| **Внешние** | Незнакомцы в ЛС или неизвестных чатах | Только перенаправление к @anylise |

Контактная книга — источник истины для определения "своих". Добавить контакт может только LLM через tool `add_contact` по запросу из чата команды. Внешний человек не может добавить себя сам.

## Частые проблемы

### `Telethon session is not authorized`
Создайте сессию: `python3 scripts/create_telethon_session.py`

### OpenClaw вернул HTTP 500
- Проверьте что gateway запущен: `pgrep -fa openclaw-gateway`
- Проверьте токен: `OPENCLAW_TOKEN` в `.env`
- Проверьте модель: `openclaw models list`

### Напоминания не доставляются
- Проверьте БД: `sqlite3 bot.db "SELECT * FROM reminders;"`
- Проверьте scheduler в логах: ищите `Tick: found N pending reminders`
- Если БД пустая — LLM не вызывает `set_reminder` tool (проблема с tool calling)

### Tool calling не работает
- OpenClaw/Codex **не поддерживает** tool calling — это нормально, он для разговоров
- Tools работают только через OpenRouter (Grok 4.1 Fast) — проверьте `LLM_API_KEY`
- Тест: отправьте "напомни через 1 минуту тест" и смотрите лог на `Tool call: set_reminder`
- Поддерживаются модели с нативным OpenAI tool-calling форматом (Grok, GPT, Gemini) и DeepSeek-style textual tool calls

### `GetForumTopicsRequest` not found
Версия Telethon не поддерживает этот метод. Топики forum-групп не синхронизируются, но отправка в топики работает через `top_msg_id`.

### Бот не отвечает в группе
Сообщения в группах обрабатываются только если начинаются с `GROUP_TRIGGER` (по умолчанию `!ai`).
