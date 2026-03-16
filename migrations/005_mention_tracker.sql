-- Track @mention counts per person per chat/topic.
-- When count reaches the limit (default 2), bot should switch to DM.
-- Resets when the mentioned person responds in the same chat/topic.

CREATE TABLE IF NOT EXISTS mention_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mentioned_username TEXT NOT NULL,       -- @username of the person being tagged
    chat_id INTEGER NOT NULL,              -- chat where tagging happens
    topic_id INTEGER,                      -- topic within forum (NULL = whole chat)
    mention_count INTEGER NOT NULL DEFAULT 0,
    last_mention_at TEXT,                  -- ISO timestamp of last mention
    resolved_at TEXT,                      -- when the person responded (NULL = still pending)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(mentioned_username, chat_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_mention_tracker_pending
    ON mention_tracker(mentioned_username, chat_id, topic_id)
    WHERE resolved_at IS NULL;
