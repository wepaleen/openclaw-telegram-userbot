"""Backward-compatible import path for the task-core scheduler."""

from apps.task_core.scheduler import start_scheduler, stop_scheduler

__all__ = ["start_scheduler", "stop_scheduler"]
