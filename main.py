"""Runtime launcher for the Telegram manager."""

from config import settings


def main() -> None:
    runtime = settings.runtime_backend
    if runtime == "pyrogram":
        from main_pyrogram import main as run_pyrogram

        run_pyrogram()
        return

    if runtime == "telethon":
        from main_telethon import main as run_telethon

        run_telethon()
        return

    raise SystemExit(
        f"Unsupported BOT_RUNTIME={runtime!r}. Expected 'telethon' or 'pyrogram'."
    )


if __name__ == "__main__":
    main()
