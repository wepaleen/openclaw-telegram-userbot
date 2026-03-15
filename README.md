# Telegram Userbot Manager

Рабочий Telegram userbot-менеджер с двумя runtime-путями:

- `telethon` — новый основной runtime: `Telethon + OpenClaw + task core`
- `pyrogram` — legacy runtime для совместимости

По умолчанию launcher в [main.py](/Users/user/ai-anylise/main.py) запускает `telethon`.

## Что Нужно Установить

- Python `3.11+`
- `pip`
- доступ к Telegram API: `API_ID` и `API_HASH`
- поднятый `OpenClaw Gateway`, доступный по `OPENCLAW_URL`

Python-зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Что ставится из `requirements.txt`:

- `telethon`
- `pyrogram`
- `httpx`
- `aiosqlite`
- `python-dotenv`

## Настройка `.env`

Минимально заполните:

```env
API_ID=123456
API_HASH=your_api_hash

OPENCLAW_URL=http://127.0.0.1:18789/v1/responses
OPENCLAW_TOKEN=your_gateway_token
OPENCLAW_AGENT_ID=main

GROUP_TRIGGER=!ai
BOT_RUNTIME=telethon
MAX_TOOL_CALLS=12

TELETHON_STRING_SESSION=
TELETHON_SESSION_NAME=openclaw_userbot_telethon
```

Опционально:

- `ALLOWED_CHAT_IDS` — список chat id через запятую
- `MAIN_FORUM_CHAT_ID` — основная forum-группа
- `BOT_DB_PATH` — путь к SQLite базе, по умолчанию `bot.db`
- `BOT_TIMEZONE` — таймзона, по умолчанию `Europe/Moscow`
- `PYROGRAM_SESSION` — имя legacy Pyrogram session

## Как Получить Telethon Session

Новый runtime требует авторизованную user-session. Проще всего создать `TELETHON_STRING_SESSION`:

```bash
source .venv/bin/activate
python3 scripts/create_telethon_session.py
```

Скрипт:

1. попросит номер телефона;
2. попросит код из Telegram;
3. если включён 2FA, попросит пароль;
4. выведет готовую строку `TELETHON_STRING_SESSION`.

Скопируйте её в `.env`.

## Как Запустить

### Основной режим: Telethon + OpenClaw

```bash
source .venv/bin/activate
python3 main.py
```

Так как `BOT_RUNTIME=telethon` выбран по умолчанию, этого достаточно.

Явный запуск того же runtime:

```bash
python3 main_telethon.py
```

### Legacy режим: Pyrogram

```bash
source .venv/bin/activate
BOT_RUNTIME=pyrogram python3 main.py
```

Или напрямую:

```bash
python3 main_pyrogram.py
```

## Что Происходит При Старте

В `telethon` runtime:

- открывается Telethon session;
- инициализируется SQLite база;
- автоматически применяются миграции из [migrations](/Users/user/ai-anylise/migrations);
- синкаются доступные диалоги и forum topics в локальный индекс;
- поднимается scheduler для reminders, deadlines и scheduled actions.

## База Данных

По умолчанию используется SQLite файл `bot.db`.

Таблицы создаются автоматически через миграции:

- `contacts`
- `chat_index`
- `topic_index`
- `tasks`
- `reminders`
- `scheduled_actions`
- `audit_log`

## Частые Проблемы

### `Telethon session is not authorized`

Сначала создайте `TELETHON_STRING_SESSION`:

```bash
python3 scripts/create_telethon_session.py
```

### `OpenClaw returned HTTP ...`

Проверьте:

- что OpenClaw gateway запущен;
- что `OPENCLAW_URL` корректный;
- что `OPENCLAW_TOKEN` действительный;
- что агент `OPENCLAW_AGENT_ID` существует.

### `database is locked`

Обычно это значит, что уже запущен другой процесс с той же session/database. Остановите старый процесс или используйте другое имя session.

### Ничего не отвечает в группе

В group/supergroup сообщения обрабатываются только если:

- они начинаются с `GROUP_TRIGGER`, по умолчанию `!ai`;
- или это соответствующее поведение legacy runtime.

## Полезные Команды Для Разработки

Проверка синтаксиса:

```bash
python3 -m py_compile $(rg --files -g '*.py')
```

Запуск основного runtime:

```bash
python3 main.py
```
