# Выбор LLM-модели для tool-calling агента

Сравнение моделей для Арсения (OpenClaw Telegram userbot).
Критерии: цена, качество tool-calling, контекст, русский язык.

> Текущая модель: `x-ai/grok-4.1-fast` — $0.20/$0.50, 2M контекст
> Предыдущая: `deepseek/deepseek-chat-v3-0324` — $0.20/$0.77, 164K контекст

## Топ рекомендаций

### 1. Gemini 2.5 Flash Lite — `google/gemini-2.5-flash-lite`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.08 | $0.40 | 1M |

- Самая дешёвая из качественных моделей
- 1M контекст — в 6x больше текущего
- Google нативно поддерживает OpenAI tool-calling формат
- Cache hit rate ~25% на OpenRouter — ещё дешевле на практике
- Lightweight, быстрая

### 2. DeepSeek V3.2 — `deepseek/deepseek-v3.2`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.26 | $0.38 | 164K |

- Самый дешёвый output ($0.38)
- Заточена под "agentic tool-use" (улучшена vs V3 0324)
- Тот же провайдер что сейчас — минимум изменений
- Может быть нестабильной (новая)

### 3. Grok 4.1 Fast — `x-ai/grok-4.1-fast`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.20 | $0.50 | 2M |

- "Best agentic tool calling model" по описанию xAI
- 2M контекст — максимальный из всех
- #1 в нескольких категориях на OpenRouter
- Дешёвая для своего уровня качества

### 4. Grok 4 Fast — `x-ai/grok-4-fast`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.20 | $0.50 | 2M |

- Предыдущее поколение Grok 4.1 Fast
- Те же цены и контекст
- Проверенная стабильность

### 5. GPT-4.1 Mini — `openai/gpt-4.1-mini`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.40 | $1.60 | 1M |

- Самый надёжный OpenAI tool-calling (стандартный формат)
- 1M контекст
- Не нужны костыли с textual tool calls
- Дороже остальных на output

### 6. GPT-5 Nano — `openai/gpt-5-nano`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.05 | $0.40 | 400K |

- Ультра-дешёвая ($0.05 input)
- 400K контекст
- "Limited reasoning depth" — может плохо справляться с длинными цепочками tool calls

### 7. gpt-oss-120b — `openai/gpt-oss-120b`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.039 | $0.19 | 131K |

- Самая дешёвая вообще ($0.039/$0.19)
- Open-weight, 117B параметров
- "Agentic" по описанию
- Может хуже следовать инструкциям на русском

### 8. Qwen3 235B Instruct — `qwen/qwen3-235b-a22b-instruct-2507`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.07 | $0.10 | 262K |

- Самый дешёвый output из всех ($0.10)
- Multilingual, 235B параметров (22B active)
- Open-weight (Alibaba)
- Риск: может быть нестабильный tool-calling (как DeepSeek)

### 9. Qwen3.5-Flash — `qwen/qwen3.5-flash`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.10 | $0.40 | 1M |

- Аналог Gemini Flash Lite по цене
- 1M контекст
- Hybrid architecture, "higher inference efficiency"

### 10. Qwen3 32B — `qwen/qwen3-32b`

| Input | Output | Контекст |
|-------|--------|----------|
| $0.08 | $0.24 | 41K |

- Очень дешёвая
- Маленький контекст (41K) — может быть мало для агентских сценариев

## Модели среднего tier (дороже, но качественнее)

| Модель | Input | Output | Контекст | Заметки |
|--------|-------|--------|----------|---------|
| Gemini 2.5 Flash | $0.30 | $2.50 | 1M | "Thinking" mode, advanced reasoning |
| Gemini 3 Flash Preview | $0.50 | $3.00 | 1M | Near Pro level, agentic workflows |
| Claude 3.5 Haiku | $0.80 | $4.00 | 200K | Быстрая, хороший tool-use |
| Claude Haiku 4.5 | $1.00 | $5.00 | 200K | Near-frontier quality |

## Премиум модели (overkill для бота)

| Модель | Input | Output | Контекст |
|--------|-------|--------|----------|
| Claude Sonnet 4.6 | $3.00 | $15.00 | 1M |
| GPT-5.2 | $1.75 | $14.00 | 400K |
| Grok 4.20 Beta | $2.00 | $6.00 | 2M |
| Gemini 2.5 Pro | $1.25 | $10.00 | 1M |

## Как переключить

```bash
# На сервере: отредактировать .env
nano .env

# Поменять строку:
LLM_MODEL=google/gemini-2.5-flash-lite

# Перезапустить бота в tmux:
# Ctrl+C, затем: python main_telethon.py
```

## Заметки

- DeepSeek модели могут возвращать tool calls как текст (textual tool calls) — в коде есть парсер для этого
- OpenAI/Google/Anthropic модели используют стандартный OpenAI tool-calling формат
- Grok модели поддерживают OpenAI-совместимый API через OpenRouter
- При смене на не-DeepSeek модель парсер textual tool calls не мешает — он срабатывает только если видит `<tool_call_begin>` в контенте
