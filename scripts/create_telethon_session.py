"""Interactive helper that creates a Telethon StringSession for the userbot runtime."""

from __future__ import annotations

import asyncio
import os
from getpass import getpass

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()


def _read_api_credentials() -> tuple[int, str]:
    raw_api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not raw_api_id or not api_hash:
        raise SystemExit("API_ID и API_HASH должны быть заданы в .env")

    if not raw_api_id.isdigit():
        raise SystemExit("API_ID должен быть числом")

    return int(raw_api_id), api_hash


async def _main() -> None:
    api_id, api_hash = _read_api_credentials()

    client = TelegramClient(
        StringSession(),
        api_id,
        api_hash,
        device_model=os.getenv("TELETHON_DEVICE_MODEL", "OpenClaw Telethon Bridge"),
        system_version=os.getenv("TELETHON_SYSTEM_VERSION", "Unknown"),
        app_version=os.getenv("TELETHON_APP_VERSION", "0.1"),
        lang_code=os.getenv("TELETHON_LANG_CODE", "ru"),
        system_lang_code=os.getenv("TELETHON_SYSTEM_LANG_CODE", "ru-RU"),
    )

    async with client:
        await client.start(
            phone=lambda: input("Telegram phone: ").strip(),
            password=lambda: getpass("2FA password (если есть, иначе Enter): "),
            code_callback=lambda: input("Code from Telegram: ").strip(),
        )
        session_string = client.session.save()

    print("\nTELETHON_STRING_SESSION=")
    print(session_string)
    print(
        "\nСкопируйте это значение в .env как TELETHON_STRING_SESSION="
        "<вставьте_строку>"
    )


if __name__ == "__main__":
    asyncio.run(_main())
