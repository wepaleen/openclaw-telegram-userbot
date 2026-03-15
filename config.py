import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()


def _parse_chat_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.add(normalize_chat_id(part))
    return ids


def normalize_chat_id(value: str | int) -> int:
    raw = str(value).strip()
    if not raw:
        raise ValueError("chat id is empty")
    if not raw.lstrip("-").isdigit():
        raise ValueError(f"invalid chat id: {value}")
    chat_id = int(raw)
    if chat_id < 0:
        return chat_id
    if raw.startswith("100") and len(raw) > 10:
        return -chat_id
    return int(f"-100{raw}")


@dataclass(frozen=True)
class Settings:
    # Telegram
    api_id: int = int(os.environ.get("API_ID", "0"))
    api_hash: str = os.environ.get("API_HASH", "")
    session_name: str = os.getenv("PYROGRAM_SESSION", "openclaw_userbot")
    telethon_session_name: str = field(
        default_factory=lambda: os.getenv(
            "TELETHON_SESSION_NAME",
            f"{os.getenv('PYROGRAM_SESSION', 'openclaw_userbot')}_telethon",
        )
    )
    telethon_string_session: str = os.getenv("TELETHON_STRING_SESSION", "")
    telethon_device_model: str = os.getenv("TELETHON_DEVICE_MODEL", "OpenClaw Telethon Bridge")
    telethon_system_version: str = os.getenv("TELETHON_SYSTEM_VERSION", "Unknown")
    telethon_app_version: str = os.getenv("TELETHON_APP_VERSION", "0.1")
    telethon_lang_code: str = os.getenv("TELETHON_LANG_CODE", "ru")
    telethon_system_lang_code: str = os.getenv("TELETHON_SYSTEM_LANG_CODE", "ru-RU")

    # OpenClaw
    openclaw_url: str = os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789/v1/responses")
    openclaw_token: str = os.environ.get("OPENCLAW_TOKEN", "")
    openclaw_agent_id: str = os.getenv("OPENCLAW_AGENT_ID", "main")

    # Bot behavior
    group_trigger: str = os.getenv("GROUP_TRIGGER", "!ai")
    max_tool_calls: int = int(os.getenv("MAX_TOOL_CALLS", "12"))
    runtime_backend: str = os.getenv("BOT_RUNTIME", "telethon").strip().lower() or "telethon"
    allowed_chat_ids: set[int] = field(
        default_factory=lambda: _parse_chat_ids(os.getenv("ALLOWED_CHAT_IDS", ""))
    )
    main_forum_chat_id: int | None = field(
        default_factory=lambda: (
            normalize_chat_id(os.environ["MAIN_FORUM_CHAT_ID"])
            if os.getenv("MAIN_FORUM_CHAT_ID", "").strip()
            else None
        )
    )

    # Database
    db_path: str = os.getenv("BOT_DB_PATH", "bot.db")
    bot_timezone: str = os.getenv("BOT_TIMEZONE", "Europe/Moscow")

    # Derived
    @property
    def chat_completions_url(self) -> str:
        return self.openclaw_url.replace("/v1/responses", "/v1/chat/completions")

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.bot_timezone)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def is_allowed_chat(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return True
        return chat_id in self.allowed_chat_ids


settings = Settings()
