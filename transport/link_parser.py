"""Parse Telegram t.me / tg:// links."""

from typing import Any
from urllib.parse import parse_qs, urlparse


def first_int(values: list[str] | None) -> int | None:
    if not values:
        return None
    raw = str(values[0]).strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return None


def parse_telegram_link(link: str) -> dict[str, Any]:
    value = str(link).strip().rstrip(".,:;")
    if not value:
        raise ValueError("link is empty")
    if value.startswith("t.me/"):
        value = f"https://{value}"

    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    reply_to_message_id = (
        first_int(query.get("thread"))
        or first_int(query.get("topic"))
        or first_int(query.get("comment"))
        or first_int(query.get("reply"))
    )

    if parsed.scheme in {"http", "https"}:
        host = parsed.netloc.lower()
        if host not in {"t.me", "www.t.me"}:
            raise ValueError("unsupported Telegram host")
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            raise ValueError("Telegram link has no target")
        if parts[0] in {"joinchat", "addstickers", "addemoji"} or parts[0].startswith("+"):
            raise ValueError("invite links are not supported")
        if parts[0] == "c":
            if len(parts) < 2 or not parts[1].isdigit():
                raise ValueError("invalid private chat link")
            target: str | int = int(f"-100{parts[1]}")
            if len(parts) >= 3 and reply_to_message_id is None and parts[2].isdigit():
                reply_to_message_id = int(parts[2])
        else:
            target = f"@{parts[0]}"
            if len(parts) >= 2 and reply_to_message_id is None and parts[1].isdigit():
                reply_to_message_id = int(parts[1])
        return {"target": target, "reply_to_message_id": reply_to_message_id, "link": value}

    if parsed.scheme == "tg":
        if parsed.netloc != "resolve":
            raise ValueError("unsupported tg:// link")
        domain = (query.get("domain") or [None])[0]
        if not domain:
            raise ValueError("tg:// link has no domain")
        target = f"@{str(domain).lstrip('@')}"
        reply_to_message_id = reply_to_message_id or first_int(query.get("post"))
        return {"target": target, "reply_to_message_id": reply_to_message_id, "link": value}

    raise ValueError("unsupported Telegram link format")
