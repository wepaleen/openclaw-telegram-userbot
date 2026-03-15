"""Entry point for the Telethon + OpenClaw userbot runtime."""

import asyncio
import logging

from apps.telethon_bridge.errors import SessionNotAuthorizedError
from apps.telethon_manager_runtime import TelethonOpenClawRuntime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def _run() -> None:
    runtime = TelethonOpenClawRuntime()
    await runtime.run()


def main() -> None:
    try:
        asyncio.run(_run())
    except SessionNotAuthorizedError as e:
        raise SystemExit(
            "Telethon session is not authorized.\n"
            "Login the user session first or provide TELETHON_STRING_SESSION."
        ) from e
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
