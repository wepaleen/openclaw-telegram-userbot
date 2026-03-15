"""Backward-compatible DB import path.

The canonical task-core implementation now lives in ``apps.task_core.db``.
"""

from apps.task_core.db import close_db, get_db

__all__ = ["close_db", "get_db"]
