"""Legacy Pyrogram entry point for the previous runtime path."""

import asyncio
import logging
import sqlite3

from pyrogram import Client

from config import settings
from db import close_db, get_db
from resolver.chats import get_forum_chat_ids, sync_chats, sync_topics
from scheduler.scheduler import start_scheduler, stop_scheduler
from transport.handler import register_handlers
from transport.telegram_api import TelegramAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main_pyrogram")

app = Client(settings.session_name, api_id=settings.api_id, api_hash=settings.api_hash)
tg_api = TelegramAPI(app)


async def _scheduler_send(chat_id: int, text: str, topic_id: int | None = None) -> None:
    if topic_id:
        meta = await tg_api.get_topic_meta(chat_id, topic_id)
        if meta:
            await app.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=meta["top_message_id"],
            )
            return
    await app.send_message(chat_id=chat_id, text=text)


async def _scheduler_execute(action_type: str, params: dict) -> dict:
    from executor.actions import Action, ActionType
    from executor.executor import ActionExecutor

    executor = ActionExecutor(tg_api)
    try:
        action_type_enum = ActionType(action_type)
    except ValueError:
        return {"error": f"unknown action: {action_type}"}

    action = Action(type=action_type_enum, params=params, source="scheduler")
    return await executor.execute(action)


async def on_startup() -> None:
    await get_db()
    log.info("Database initialized at %s", settings.db_path)

    try:
        await sync_chats(tg_api)
        for chat_id in await get_forum_chat_ids():
            try:
                await sync_topics(tg_api, chat_id)
            except Exception as topic_error:
                log.warning("Initial topic sync failed for %s: %s", chat_id, topic_error)
        log.info("Initial chat sync complete")
    except Exception as e:
        log.warning("Initial chat sync failed: %s", e)

    start_scheduler(_scheduler_send, _scheduler_execute, interval=30)
    log.info("Scheduler started")


register_handlers(app, tg_api)


def main() -> None:
    try:
        app.start()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(on_startup())
        log.info("Pyrogram runtime is running. Press Ctrl+C to stop.")
        from pyrogram import idle

        loop.run_until_complete(idle())
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e).lower():
            raise SystemExit(
                "Pyrogram session database is locked. "
                "Stop the old process or use a different PYROGRAM_SESSION."
            ) from e
        raise
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        stop_scheduler()
        app.stop()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(close_db())
        log.info("Bye.")


if __name__ == "__main__":
    main()
