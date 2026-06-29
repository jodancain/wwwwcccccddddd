SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    username TEXT PRIMARY KEY,
    alias TEXT DEFAULT '',
    nickname TEXT DEFAULT '',
    remark TEXT DEFAULT '',
    is_group INTEGER DEFAULT 0,
    avatar_url TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wechat_local_id INTEGER,
    msg_svr_id TEXT UNIQUE,
    talker TEXT NOT NULL,
    sender TEXT DEFAULT '',
    type INTEGER DEFAULT 1,
    type_name TEXT DEFAULT 'text',
    is_sender INTEGER DEFAULT 0,
    content TEXT DEFAULT '',
    display_content TEXT DEFAULT '',
    create_time INTEGER NOT NULL,
    create_date TEXT NOT NULL,
    is_group INTEGER DEFAULT 0,
    FOREIGN KEY (talker) REFERENCES contacts(username)
);

CREATE INDEX IF NOT EXISTS idx_messages_create_date ON messages(create_date);
CREATE INDEX IF NOT EXISTS idx_messages_talker_time ON messages(talker, create_time);
CREATE INDEX IF NOT EXISTS idx_messages_create_time ON messages(create_time);

CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_sync_timestamp INTEGER DEFAULT 0,
    last_sync_at DATETIME,
    last_msg_count INTEGER DEFAULT 0,
    total_messages INTEGER DEFAULT 0
);

INSERT OR IGNORE INTO sync_state (id) VALUES (1);

CREATE TABLE IF NOT EXISTS ai_sessions (
    id TEXT PRIMARY KEY,
    talker TEXT DEFAULT '',
    title TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES ai_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_ai_messages_session ON ai_messages(session_id);

CREATE TABLE IF NOT EXISTS chat_apis (
    id TEXT PRIMARY KEY,
    talker TEXT NOT NULL,
    api_key TEXT NOT NULL UNIQUE,
    name TEXT DEFAULT '',
    scope TEXT DEFAULT 'records',
    permissions TEXT DEFAULT 'records:read',
    last_used_at DATETIME,
    enabled INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '',
    description TEXT DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_pending_actions (
    id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    confirmed_at DATETIME
);

CREATE TABLE IF NOT EXISTS agent_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT DEFAULT 'wechat',
    talker TEXT DEFAULT '',
    title TEXT DEFAULT '',
    text TEXT NOT NULL,
    start_message_id INTEGER DEFAULT 0,
    end_message_id INTEGER DEFAULT 0,
    start_time INTEGER DEFAULT 0,
    end_time INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_talker_time
ON knowledge_chunks(talker, end_time);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_message_range
ON knowledge_chunks(start_message_id, end_message_id);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
USING fts5(title, text, talker, content='knowledge_chunks', content_rowid='id');

CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    chunk_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chunk_id, model),
    FOREIGN KEY (chunk_id) REFERENCES knowledge_chunks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_model
ON knowledge_embeddings(model, dimensions);

CREATE TABLE IF NOT EXISTS message_source_enrichments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    extracted_text TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    status TEXT DEFAULT 'ok',
    error TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(message_id, kind, source_key),
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_message_source_enrichments_message
ON message_source_enrichments(message_id);
"""
