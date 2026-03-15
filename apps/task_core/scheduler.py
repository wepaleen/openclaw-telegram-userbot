"""Scheduler engine for reminders, deadlines and scheduled actions."""

import asyncio
import logging
from datetime import datetime, timezone

from apps.task_core.store.task_store import (
    compute_next_recurrence_fire_at,
    create_reminder,
    format_local_datetime,
    get_due_tasks,
    get_pending_actions,
    get_pending_reminders,
    mark_action_executed,
    mark_reminder_fired,
    mark_task_deadline_notified,
)

log = logging.getLogger("task_core.scheduler")

_running = False
_task: asyncio.Task | None = None


async def _tick(send_fn, execute_fn) -> None:
    """Check for pending reminders, deadlines and scheduled actions."""
    now = datetime.now(timezone.utc).isoformat()

    reminders = await get_pending_reminders(now)
    for reminder in reminders:
        try:
            text = reminder["text"]
            if reminder.get("target_user"):
                text = f"{reminder['target_user']} {text}"
            await send_fn(
                chat_id=reminder["target_chat_id"],
                text=f"🔔 Напоминание: {text}",
                topic_id=reminder.get("target_topic_id"),
            )
            await mark_reminder_fired(reminder["id"])
            recurrence = reminder.get("recurrence")
            if recurrence:
                next_fire_at = compute_next_recurrence_fire_at(
                    reminder.get("fire_at"),
                    recurrence,
                )
                if next_fire_at:
                    await create_reminder(
                        text=reminder["text"],
                        fire_at=next_fire_at,
                        target_chat_id=reminder["target_chat_id"],
                        target_topic_id=reminder.get("target_topic_id"),
                        target_user=reminder.get("target_user"),
                        recurrence=recurrence,
                        task_id=reminder.get("task_id"),
                    )
                    log.info(
                        "Rescheduled recurring reminder %d -> %s",
                        reminder["id"],
                        next_fire_at,
                    )
                else:
                    log.warning(
                        "Reminder %d has unsupported recurrence: %s",
                        reminder["id"],
                        recurrence,
                    )
            log.info("Fired reminder %d: %s", reminder["id"], reminder["text"][:50])
        except Exception as e:
            log.error("Failed to fire reminder %d: %s", reminder["id"], e)

    tasks = await get_due_tasks(now)
    for task in tasks:
        try:
            due_local = format_local_datetime(task.get("due_at")) or task.get("due_at")
            text = f"⏰ Дедлайн по задаче #{task['id']}: {task['title']}"
            if task.get("assignee"):
                text += f" → {task['assignee']}"
            if due_local:
                text += f" (срок: {due_local})"

            if task.get("source_chat_id"):
                await send_fn(chat_id=task["source_chat_id"], text=text)
            await mark_task_deadline_notified(task["id"])
            log.info("Deadline notified for task %d", task["id"])
        except Exception as e:
            log.error("Failed to notify deadline for task %d: %s", task["id"], e)

    actions = await get_pending_actions(now)
    for action in actions:
        try:
            result = await execute_fn(action["action_type"], action["action_params"])
            error = result.get("error") if isinstance(result, dict) else None
            await mark_action_executed(action["id"], error=error)
            if error:
                log.error("Scheduled action %d failed logically: %s", action["id"], error)
            else:
                log.info(
                    "Executed scheduled action %d: %s",
                    action["id"],
                    action["action_type"],
                )
        except Exception as e:
            await mark_action_executed(action["id"], error=str(e))
            log.error("Failed scheduled action %d: %s", action["id"], e)


async def _loop(send_fn, execute_fn, interval: int = 30) -> None:
    """Main scheduler loop. Runs every `interval` seconds."""
    global _running
    _running = True
    log.info("Scheduler started (interval=%ds)", interval)

    while _running:
        try:
            await _tick(send_fn, execute_fn)
        except Exception as e:
            log.error("Scheduler tick error: %s", e)
        await asyncio.sleep(interval)


def start_scheduler(send_fn, execute_fn, interval: int = 30) -> None:
    """Start the scheduler as a background asyncio task."""
    global _task
    if _task and not _task.done():
        log.warning("Scheduler already running")
        return
    _task = asyncio.get_event_loop().create_task(_loop(send_fn, execute_fn, interval))


def stop_scheduler() -> None:
    global _running, _task
    _running = False
    if _task:
        _task.cancel()
        _task = None
    log.info("Scheduler stopped")
