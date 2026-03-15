"""Backward-compatible import path for task-core audit helpers."""

from apps.task_core.audit import Timer, log_action

__all__ = ["Timer", "log_action"]
