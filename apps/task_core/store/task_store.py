"""CRUD for tasks, reminders and scheduled actions in the task core."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.task_core.db import get_db
from config import settings

log = logging.getLogger("task_core.store")


async def create_task(
    title: str,
    description: str | None = None,
    assignee: str | None = None,
    due_at: str | None = None,
    priority: str = "normal",
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
) -> dict[str, Any]:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO tasks (title, description, assignee, due_at, priority, "
        "source_chat_id, source_message_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            title,
            description,
            assignee,
            due_at,
            priority,
            source_chat_id,
            source_message_id,
        ),
    )
    await db.commit()
    return {"ok": True, "task_id": cursor.lastrowid, "title": title}


async def list_tasks(
    status: str | None = None,
    assignee: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    db = await get_db()
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list[Any] = []
    if status:
        query += " AND status = ?"
        params.append(status)
    else:
        query += " AND status != 'cancelled'"
    if assignee:
        query += " AND LOWER(assignee) LIKE ?"
        params.append(f"%{assignee.lower()}%")
    query += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
    query += "WHEN 'normal' THEN 2 ELSE 3 END, due_at ASC NULLS LAST LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(query, params)
    return [dict(r) for r in rows]


async def update_task(task_id: int, **kwargs: Any) -> dict[str, Any]:
    db = await get_db()
    sets = []
    params: list[Any] = []
    for key, val in kwargs.items():
        if key in ("status", "title", "description", "assignee", "due_at", "priority"):
            sets.append(f"{key} = ?")
            params.append(val)
    if not sets:
        return {"error": "nothing to update"}
    sets.append("updated_at = datetime('now')")
    params.append(task_id)
    await db.execute(
        f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    await db.commit()
    return {"ok": True, "task_id": task_id}


async def complete_task(task_id: int) -> dict[str, Any]:
    return await update_task(task_id, status="done")


async def get_due_tasks(before: str) -> list[dict[str, Any]]:
    """Return open tasks whose deadline has passed and were not notified yet."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM tasks "
        "WHERE status IN ('open', 'in_progress') "
        "AND due_at IS NOT NULL "
        "AND due_at <= ? "
        "AND deadline_notified_at IS NULL "
        "ORDER BY due_at ASC",
        (before,),
    )
    return [dict(r) for r in rows]


async def mark_task_deadline_notified(task_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE tasks SET deadline_notified_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), task_id),
    )
    await db.commit()


async def create_reminder(
    text: str,
    fire_at: str,
    target_chat_id: int,
    target_topic_id: int | None = None,
    target_user: str | None = None,
    recurrence: str | None = None,
    task_id: int | None = None,
) -> dict[str, Any]:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO reminders (text, fire_at, target_chat_id, target_topic_id, "
        "target_user, recurrence, task_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            text,
            fire_at,
            target_chat_id,
            target_topic_id,
            target_user,
            recurrence,
            task_id,
        ),
    )
    await db.commit()
    return {"ok": True, "reminder_id": cursor.lastrowid, "fire_at": fire_at}


async def list_reminders(
    status: str | None = "pending",
    limit: int = 20,
) -> list[dict[str, Any]]:
    db = await get_db()
    query = "SELECT * FROM reminders WHERE 1=1"
    params: list[Any] = []
    normalized_status = (status or "").strip().lower()
    if normalized_status and normalized_status != "all":
        query += " AND status = ?"
        params.append(normalized_status)
    query += " ORDER BY fire_at ASC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(query, params)
    return [dict(r) for r in rows]


async def get_pending_reminders(before: str) -> list[dict[str, Any]]:
    """Get reminders that should fire before the given ISO datetime."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM reminders WHERE status = 'pending' AND fire_at <= ? "
        "ORDER BY fire_at ASC",
        (before,),
    )
    return [dict(r) for r in rows]


async def mark_reminder_fired(reminder_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE reminders SET status = 'fired' WHERE id = ?",
        (reminder_id,),
    )
    await db.commit()


async def cancel_reminder(reminder_id: int) -> dict[str, Any]:
    db = await get_db()
    await db.execute(
        "UPDATE reminders SET status = 'cancelled' WHERE id = ?",
        (reminder_id,),
    )
    await db.commit()
    return {"ok": True, "reminder_id": reminder_id}


async def create_scheduled_action(
    action_type: str,
    action_params: dict,
    execute_at: str,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
) -> dict[str, Any]:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO scheduled_actions "
        "(action_type, action_params, execute_at, source_chat_id, source_message_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            action_type,
            json.dumps(action_params, ensure_ascii=False),
            execute_at,
            source_chat_id,
            source_message_id,
        ),
    )
    await db.commit()
    return {"ok": True, "scheduled_id": cursor.lastrowid, "execute_at": execute_at}


async def list_scheduled_actions(
    status: str | None = "pending",
    limit: int = 20,
) -> list[dict[str, Any]]:
    db = await get_db()
    query = "SELECT * FROM scheduled_actions WHERE 1=1"
    params: list[Any] = []
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY execute_at ASC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(query, params)
    result = []
    for row in rows:
        item = dict(row)
        item["action_params"] = json.loads(item.get("action_params", "{}"))
        result.append(item)
    return result


async def cancel_scheduled_action(action_id: int) -> dict[str, Any]:
    db = await get_db()
    await db.execute(
        "UPDATE scheduled_actions SET status = 'cancelled' "
        "WHERE id = ? AND status = 'pending'",
        (action_id,),
    )
    await db.commit()
    return {"ok": True, "scheduled_id": action_id}


async def get_pending_actions(before: str) -> list[dict[str, Any]]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM scheduled_actions WHERE status = 'pending' AND execute_at <= ? "
        "ORDER BY execute_at ASC",
        (before,),
    )
    result = []
    for row in rows:
        item = dict(row)
        item["action_params"] = json.loads(item.get("action_params", "{}"))
        result.append(item)
    return result


async def mark_action_executed(action_id: int, error: str | None = None) -> None:
    db = await get_db()
    status = "failed" if error else "executed"
    await db.execute(
        "UPDATE scheduled_actions SET status = ?, error = ? WHERE id = ?",
        (status, error, action_id),
    )
    await db.commit()


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _local_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(settings.tzinfo)
    if now.tzinfo is None:
        return now.replace(tzinfo=settings.tzinfo)
    return now.astimezone(settings.tzinfo)


def parse_time_delta(delta_str: str) -> timedelta | None:
    """Parse '30 мин', '2 часа', '1h', '45m' etc."""
    import re

    match = re.match(
        r"(\d+)\s*(мин|минут|м|min|minutes?|m|час|часов|часа|ч|hour|hours?|h)",
        delta_str.strip(),
        re.IGNORECASE,
    )
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit in ("мин", "минут", "м", "min", "minute", "minutes", "m"):
        return timedelta(minutes=value)
    if unit in ("час", "часов", "часа", "ч", "hour", "hours", "h"):
        return timedelta(hours=value)
    return None


def parse_recurrence_interval(value: str | None) -> timedelta | None:
    """Parse recurring intervals like 'каждый день' or 'every 2 hours'."""
    import re

    if not value:
        return None

    raw = str(value).strip().lower()
    if not raw:
        return None

    alias_map = {
        "hourly": timedelta(hours=1),
        "every hour": timedelta(hours=1),
        "каждый час": timedelta(hours=1),
        "ежечасно": timedelta(hours=1),
        "daily": timedelta(days=1),
        "every day": timedelta(days=1),
        "каждый день": timedelta(days=1),
        "ежедневно": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "every week": timedelta(weeks=1),
        "каждую неделю": timedelta(weeks=1),
        "еженедельно": timedelta(weeks=1),
    }
    if raw in alias_map:
        return alias_map[raw]

    match = re.match(
        r"^(?:every|каждые?|раз в)\s+(\d+)\s*"
        r"(minutes?|mins?|hours?|days?|weeks?|"
        r"мин(?:ут[аы]?)?|час(?:а|ов)?|дн(?:я|ей)?|недел(?:ю|и|ь))$",
        raw,
        re.IGNORECASE,
    )
    if not match:
        return None

    value_num = int(match.group(1))
    unit = match.group(2).lower()
    if unit in {"minute", "minutes", "min", "mins", "мин", "минута", "минуты", "минут"}:
        return timedelta(minutes=value_num)
    if unit in {"hour", "hours", "час", "часа", "часов"}:
        return timedelta(hours=value_num)
    if unit in {"day", "days", "дня", "дней"}:
        return timedelta(days=value_num)
    if unit in {"week", "weeks", "неделю", "недели", "недель"}:
        return timedelta(weeks=value_num)
    return None


def parse_time_of_day(time_str: str) -> tuple[int, int] | None:
    """Parse '14:30', '14.30' etc."""
    import re

    match = re.match(r"(\d{1,2})[:.]\s*(\d{2})", time_str.strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if 0 <= hours <= 23 and 0 <= minutes <= 59:
        return (hours, minutes)
    return None


def parse_datetime_input(value: str | None, now: datetime | None = None) -> str | None:
    """Parse user time expressions into UTC ISO timestamps."""
    import re

    if not value:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    local_now = _local_now(now)

    delta = parse_time_delta(raw)
    if delta:
        return _to_utc_iso(local_now + delta)

    today_match = re.match(
        r"^(?:сегодня|today)(?:\s+в)?\s*(\d{1,2}[:.]\d{2})?$",
        raw,
        re.IGNORECASE,
    )
    if today_match:
        hm = parse_time_of_day(today_match.group(1) or "23:59")
        if hm:
            target = local_now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            return _to_utc_iso(target)

    tomorrow_match = re.match(
        r"^(?:завтра|tomorrow)(?:\s+в)?\s*(\d{1,2}[:.]\d{2})?$",
        raw,
        re.IGNORECASE,
    )
    if tomorrow_match:
        hm = parse_time_of_day(tomorrow_match.group(1) or "09:00")
        if hm:
            target = (local_now + timedelta(days=1)).replace(
                hour=hm[0],
                minute=hm[1],
                second=0,
                microsecond=0,
            )
            return _to_utc_iso(target)

    hm = parse_time_of_day(raw)
    if hm:
        target = local_now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if target <= local_now:
            target += timedelta(days=1)
        return _to_utc_iso(target)

    for fmt, default_time in (
        ("%Y-%m-%d %H:%M", None),
        ("%Y-%m-%dT%H:%M", None),
        ("%d.%m.%Y %H:%M", None),
        ("%d.%m %H:%M", None),
        ("%Y-%m-%d", (23, 59)),
        ("%d.%m.%Y", (23, 59)),
        ("%d.%m", (23, 59)),
    ):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue

        year = parsed.year
        if "%d.%m" in fmt and "%Y" not in fmt:
            year = local_now.year
        parsed = parsed.replace(year=year)
        if default_time:
            parsed = parsed.replace(hour=default_time[0], minute=default_time[1])
        localized = parsed.replace(tzinfo=settings.tzinfo)
        if fmt == "%d.%m" and localized < local_now.replace(second=0, microsecond=0):
            localized = localized.replace(year=year + 1)
        return _to_utc_iso(localized)

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=settings.tzinfo)
    return _to_utc_iso(parsed)


def compute_next_recurrence_fire_at(
    current_fire_at: str | None,
    recurrence: str | None,
    *,
    now: datetime | None = None,
) -> str | None:
    """Compute the next UTC fire time for a recurring reminder."""
    if not current_fire_at or not recurrence:
        return None

    interval = parse_recurrence_interval(recurrence)
    if interval is None:
        return None

    try:
        next_fire = datetime.fromisoformat(current_fire_at)
    except ValueError:
        return None

    if next_fire.tzinfo is None:
        next_fire = next_fire.replace(tzinfo=timezone.utc)

    reference = now or datetime.now(timezone.utc)
    while next_fire <= reference:
        next_fire += interval
    return next_fire.astimezone(timezone.utc).isoformat()


def format_local_datetime(value: str | None) -> str | None:
    """Format stored UTC ISO datetime in configured local timezone."""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(settings.tzinfo).strftime("%Y-%m-%d %H:%M")
