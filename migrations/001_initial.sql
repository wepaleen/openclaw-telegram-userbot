PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Contact book: maps human names to Telegram identifiers
CREATE TABLE IF NOT EXISTS contacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name  TEXT NOT NULL,
    username      TEXT,
    user_id       INTEGER,
    phone         TEXT,
    notes         TEXT,
    aliases       TEXT DEFAULT '[]',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_username ON contacts(username) WHERE username IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_user_id ON contacts(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_display_name ON contacts(display_name COLLATE NOCASE);

-- Chat index: cached list of accessible chats
CREATE TABLE IF NOT EXISTS chat_index (
    chat_id       INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    username      TEXT,
    chat_type     TEXT NOT NULL DEFAULT 'group',
    is_forum      INTEGER NOT NULL DEFAULT 0,
    aliases       TEXT DEFAULT '[]',
    last_synced   TEXT DEFAULT (datetime('now'))
);

-- Topic index for forum chats
CREATE TABLE IF NOT EXISTS topic_index (
    chat_id       INTEGER NOT NULL,
    topic_id      INTEGER NOT NULL,
    title         TEXT NOT NULL,
    top_message_id INTEGER,
    last_synced   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, topic_id),
    FOREIGN KEY (chat_id) REFERENCES chat_index(chat_id) ON DELETE CASCADE
);

-- Tasks
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    priority      TEXT DEFAULT 'normal',
    assignee      TEXT,
    due_at        TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    source_chat_id    INTEGER,
    source_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at) WHERE due_at IS NOT NULL;

-- Reminders
CREATE TABLE IF NOT EXISTS reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL,
    fire_at       TEXT NOT NULL,
    recurrence    TEXT,
    target_chat_id    INTEGER NOT NULL,
    target_topic_id   INTEGER,
    target_user       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT DEFAULT (datetime('now')),
    task_id       INTEGER,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_fire ON reminders(fire_at) WHERE status = 'pending';

-- Scheduled actions
CREATE TABLE IF NOT EXISTS scheduled_actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type   TEXT NOT NULL,
    action_params TEXT NOT NULL DEFAULT '{}',
    execute_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    error         TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    source_chat_id    INTEGER,
    source_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_scheduled_status ON scheduled_actions(execute_at) WHERE status = 'pending';

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT DEFAULT (datetime('now')),
    action_type   TEXT NOT NULL,
    intent        TEXT,
    source_chat_id    INTEGER,
    source_message_id INTEGER,
    source_user_id    INTEGER,
    target_chat_id    INTEGER,
    target_topic_id   INTEGER,
    params        TEXT DEFAULT '{}',
    result        TEXT DEFAULT '{}',
    success       INTEGER NOT NULL DEFAULT 1,
    error         TEXT,
    llm_used      INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_type);
