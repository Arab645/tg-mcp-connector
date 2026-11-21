# src/tg_mcp/storage/migrations.py
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chats (
  chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('group','dm','channel')),
  watched INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS messages (
  chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
  sender_id INTEGER, sender_name TEXT, date_utc TEXT NOT NULL,
  text TEXT, media_kind TEXT, reply_to INTEGER, raw_json TEXT NOT NULL,
  PRIMARY KEY (chat_id, message_id));
CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON messages(chat_id, date_utc);

CREATE TABLE IF NOT EXISTS sync_cursors (
  chat_id INTEGER PRIMARY KEY, last_message_id INTEGER NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS summaries (
  chat_id INTEGER NOT NULL, date TEXT NOT NULL,
  status TEXT NOT NULL, last_covered_id INTEGER,
  content_json TEXT, notion_page_id TEXT,
  model TEXT, tokens_in INTEGER, tokens_out INTEGER,
  error TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY (chat_id, date));

CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL, finished_at TEXT,
  trigger TEXT NOT NULL, stats_json TEXT, error TEXT);

CREATE TABLE IF NOT EXISTS daily_digests (
  date            TEXT PRIMARY KEY,
  status          TEXT NOT NULL,
  covered_hash    TEXT,
  content_md      TEXT,
  notion_page_id  TEXT,
  model           TEXT,
  tokens_in       INTEGER,
  tokens_out      INTEGER,
  error           TEXT,
  updated_at      TEXT NOT NULL
);
"""
