ALTER TABLE tasks ADD COLUMN deadline_notified_at TEXT;

CREATE INDEX IF NOT EXISTS idx_tasks_deadline_notified
ON tasks(due_at)
WHERE due_at IS NOT NULL AND deadline_notified_at IS NULL;
