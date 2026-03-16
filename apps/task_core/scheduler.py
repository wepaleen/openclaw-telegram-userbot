"""Scheduler engine for reminders, deadlines and scheduled actions."""

import asyncio
import logging
from datetime import datetime, timezone

from apps.task_core.store.session_cache import clear_old_sessions
from apps.task_core.store.task_store import (
    compute_next_recurrence_fire_at,
    create_reminder,
    create_scheduled_action,
    format_local_datetime,
    get_due_tasks,
    get_pending_actions,
    get_pending_reminders,
    mark_action_executed,
    mark_reminder_fired,
    mark_task_deadline_notified,
)
from apps.task_core.audit import log_action

log = logging.getLogger("task_core.scheduler")

_running = False
_task: asyncio.Task | None = None


async def _tick(send_fn, execute_fn) -> None:
    """Check for pending reminders, deadlines and scheduled actions."""
    now = datetime.now(timezone.utc).isoformat()

    reminders = await get_pending_reminders(now)
    if reminders:
        log.info("Tick: found %d pending reminders (now=%s)", len(reminders), now)
    for reminder in reminders:
        log.info(
            "Firing reminder %d: chat_id=%s target_user=%s topic_id=%s text=%s",
            reminder["id"],
            reminder.get("target_chat_id"),
            reminder.get("target_user"),
            reminder.get("target_topic_id"),
            str(reminder.get("text", ""))[:80],
        )
        try:
            mention_raw = reminder.get("mention_username") or ""
            mention_tag = ""
            if mention_raw:
                mention_tag = mention_raw if mention_raw.startswith("@") else f"@{mention_raw}"

            reminder_text = f"🔔 {mention_tag} Напоминание: {reminder['text']}" if mention_tag else f"🔔 Напоминание: {reminder['text']}"

            # If reminder targets a specific person, send to their DM instead of the group thread
            if mention_raw and reminder.get("target_topic_id"):
                # Send to DM — use mention_username as target, no topic
                target_user = mention_raw.lstrip("@")
                log.info(
                    "Reminder %d: sending to DM of @%s instead of topic %s",
                    reminder["id"], target_user, reminder.get("target_topic_id"),
                )
                await send_fn(
                    chat_id=reminder["target_chat_id"],
                    target=target_user,
                    text=f"🔔 Напоминание: {reminder['text']}",
                    topic_id=None,
                )
            else:
                await send_fn(
                    chat_id=reminder["target_chat_id"],
                    target=reminder.get("target_user"),
                    text=reminder_text,
                    topic_id=reminder.get("target_topic_id"),
                )
            await mark_reminder_fired(reminder["id"])
            await log_action(
                action_type="deliver_reminder",
                source_chat_id=reminder.get("target_chat_id"),
                target_chat_id=reminder.get("target_chat_id"),
                target_topic_id=reminder.get("target_topic_id"),
                params={
                    "reminder_id": reminder["id"],
                    "target_user": reminder.get("target_user"),
                },
                result={"ok": True, "fire_at": reminder.get("fire_at")},
                success=True,
                llm_used=False,
            )
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
                        source_sender_username=reminder.get("source_sender_username"),
                        mention_username=reminder.get("mention_username"),
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
            await log_action(
                action_type="deliver_reminder",
                source_chat_id=reminder.get("target_chat_id"),
                target_chat_id=reminder.get("target_chat_id"),
                target_topic_id=reminder.get("target_topic_id"),
                params={
                    "reminder_id": reminder["id"],
                    "target_user": reminder.get("target_user"),
                },
                result={},
                success=False,
                error=str(e),
                llm_used=False,
            )
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
            # Reschedule recurring actions
            recurrence = action.get("recurrence")
            if recurrence and not error:
                next_at = compute_next_recurrence_fire_at(
                    action.get("execute_at"), recurrence,
                )
                if next_at:
                    await create_scheduled_action(
                        action_type=action["action_type"],
                        action_params=action["action_params"],
                        execute_at=next_at,
                        source_chat_id=action.get("source_chat_id"),
                        source_message_id=action.get("source_message_id"),
                        recurrence=recurrence,
                    )
                    log.info(
                        "Rescheduled recurring action %d -> %s",
                        action["id"], next_at,
                    )
        except Exception as e:
            await mark_action_executed(action["id"], error=str(e))
            log.error("Failed scheduled action %d: %s", action["id"], e)


async def _loop(
    send_fn,
    execute_fn,
    interval: int = 30,
    sync_fn=None,
    sync_every: int = 120,
) -> None:
    """Main scheduler loop. Runs every `interval` seconds."""
    global _running
    _running = True
    tick_count = 0
    log.info("Scheduler started (interval=%ds, sync_every=%d ticks)", interval, sync_every)

    while _running:
        try:
            await _tick(send_fn, execute_fn)
        except Exception as e:
            log.error("Scheduler tick error: %s", e)

        tick_count += 1
        if tick_count % sync_every == 0:
            if sync_fn:
                try:
                    await sync_fn()
                    log.info("Periodic index sync completed (tick %d)", tick_count)
                except Exception as e:
                    log.warning("Periodic index sync failed: %s", e)
            try:
                deleted = await clear_old_sessions(max_age_hours=24)
                if deleted:
                    log.info("Cleared %d stale session caches", deleted)
            except Exception as e:
                log.warning("Session cache cleanup failed: %s", e)

        await asyncio.sleep(interval)


def start_scheduler(
    send_fn,
    execute_fn,
    interval: int = 30,
    sync_fn=None,
    sync_every: int = 120,
) -> None:
    """Start the scheduler as a background asyncio task."""
    global _task
    if _task and not _task.done():
        log.warning("Scheduler already running")
        return
    _task = asyncio.get_event_loop().create_task(
        _loop(send_fn, execute_fn, interval, sync_fn=sync_fn, sync_every=sync_every)
    )


def stop_scheduler() -> None:
    global _running, _task
    _running = False
    if _task:
        _task.cancel()
        _task = None
    log.info("Scheduler stopped")
