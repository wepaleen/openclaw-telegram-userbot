ALTER TABLE reminders ADD COLUMN source_sender_username TEXT;
ALTER TABLE reminders ADD COLUMN mention_username TEXT;

CREATE TABLE IF NOT EXISTS session_cache (
    session_key   TEXT PRIMARY KEY,
    messages      TEXT NOT NULL,
    updated_at    TEXT DEFAULT (datetime('now'))
);
