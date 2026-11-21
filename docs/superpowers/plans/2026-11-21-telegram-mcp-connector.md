# Telegram MCP Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python MCP server that incrementally syncs Telegram groups + DMs via MTProto, produces daily per-chat summaries with Claude Haiku 5.5, and stores them in Notion with SQLite as the source of truth.

**Architecture:** MCP server (stdio) exposes tools (list/send/sync/read-summary). A Telethon-backed sync engine pulls new messages into SQLite using `INSERT OR IGNORE` on `(chat_id, message_id)` with cursor advancement in the same transaction. A summarizer pipeline finds `(chat, date)` buckets with new content, filters noise, calls Claude, upserts a summary row and a Notion page keyed by `(chat_id, date)`.

**Tech Stack:** Python 3.11, Telethon, modelcontextprotocol python SDK (`mcp`), anthropic SDK, httpx (Notion), APScheduler, pydantic-settings, sqlite3 (stdlib) with WAL, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-21-telegram-mcp-connector-design.md`

## Global Constraints

- Python version floor: 3.11. `pyproject.toml` must pin `requires-python = ">=3.11"`.
- SQLite connections must enable WAL (`PRAGMA journal_mode=WAL`) and `PRAGMA foreign_keys=ON`.
- Every secret is read from env via `pydantic-settings`. Secrets are never logged. Session file mode is 0600.
- Dedup identity for a message: `(chat_id, message_id)`. For a summary: `(chat_id, date)` where `date` is `YYYY-MM-DD` in UTC.
- Summarization model id: `claude-haiku-5-5`.
- MCP transport: stdio only.
- All timestamps stored as ISO-8601 UTC strings.
- All tasks end with a git commit. Attribution trailer `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` on every commit.
- Default polling interval: 15 minutes. Default max backfill days on first run: 7.

## File Map

Created by this plan:

- `pyproject.toml`, `.env.example`, `README.md`, `scripts/demo.sh`
- `src/tg_mcp/__init__.py`
- `src/tg_mcp/config.py` — settings (Task 1)
- `src/tg_mcp/storage/db.py` — SQLite connection + CRUD (Task 2)
- `src/tg_mcp/storage/migrations.py` — schema DDL (Task 2)
- `src/tg_mcp/telegram/client.py` — Telethon wrapper + Protocol (Task 3)
- `src/tg_mcp/sync/engine.py` — sync engine (Task 4)
- `src/tg_mcp/summarize/filter.py` — noise filter (Task 5)
- `src/tg_mcp/storage/notion.py` — Notion upsert (Task 6)
- `src/tg_mcp/summarize/prompts.py` — prompt templates (Task 7)
- `src/tg_mcp/summarize/claude.py` — Anthropic client (Task 7)
- `src/tg_mcp/summarize/pipeline.py` — day-bucket summarizer (Task 8)
- `src/tg_mcp/tools/handlers.py` — MCP tool implementations (Task 9)
- `src/tg_mcp/server.py` — MCP stdio entrypoint (Task 9)
- `src/tg_mcp/sync/scheduler.py` — APScheduler wiring (Task 10)
- `src/tg_mcp/login.py` — one-off Telegram login CLI (Task 11)
- `tests/` mirrors `src/`

---

## Task 0: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `.env.example`, `src/tg_mcp/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

**Interfaces:**
- Produces: layout + dev dependencies that every later task uses.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "tg-mcp-connector"
version = "0.1.0"
description = "Telegram MCP connector with daily LLM summaries"
requires-python = ">=3.11"
dependencies = [
  "mcp>=1.2.0",
  "telethon>=1.36",
  "anthropic>=0.40",
  "httpx>=0.27",
  "apscheduler>=3.10",
  "pydantic>=2.6",
  "pydantic-settings>=2.2",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.5"]

[project.scripts]
tg-mcp = "tg_mcp.server:main"
tg-mcp-login = "tg_mcp.login:main"

[tool.hatch.build.targets.wheel]
packages = ["src/tg_mcp"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Write `.env.example`**

```dotenv
# Telegram (get from https://my.telegram.org)
TG_API_ID=
TG_API_HASH=
TG_SESSION_PATH=~/.config/tg-mcp/session

# Anthropic
ANTHROPIC_API_KEY=
SUMMARY_MODEL=claude-haiku-5-5

# Notion
NOTION_TOKEN=
NOTION_DATABASE_ID=

# State
TG_MCP_DB_PATH=./data/tg_mcp.sqlite3

# Behavior
SYNC_INTERVAL_MIN=15
MAX_BACKFILL_DAYS=7
WATCHLIST_DEFAULT=
```

- [ ] **Step 3: Create empty package files**

```bash
mkdir -p src/tg_mcp/{storage,telegram,sync,summarize,tools} tests scripts data
touch src/tg_mcp/__init__.py tests/__init__.py
for d in storage telegram sync summarize tools; do touch "src/tg_mcp/$d/__init__.py"; done
```

- [ ] **Step 4: Write `tests/conftest.py` with an in-memory SQLite fixture**

```python
import sqlite3
import pytest

@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
```

- [ ] **Step 5: Install and verify**

Run: `pip install -e ".[dev]" && pytest -q`
Expected: `0 tests ran` (no errors).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .env.example src/ tests/ scripts/ data/.gitkeep 2>/dev/null; \
  git add pyproject.toml .env.example src tests
touch data/.gitkeep && git add -f data/.gitkeep
git commit -m "chore: scaffold tg-mcp-connector project

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 1: Config module

**Files:**
- Create: `src/tg_mcp/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` pydantic model with fields `tg_api_id: int`, `tg_api_hash: str`, `tg_session_path: Path`, `anthropic_api_key: str`, `summary_model: str`, `notion_token: str`, `notion_database_id: str`, `tg_mcp_db_path: Path`, `sync_interval_min: int = 15`, `max_backfill_days: int = 7`, `watchlist_default: list[int] = []`. Function `load_settings() -> Settings`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_config.py
from pathlib import Path
import os
from tg_mcp.config import load_settings

def test_loads_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "abc")
    monkeypatch.setenv("TG_SESSION_PATH", str(tmp_path/"s"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("NOTION_TOKEN", "ntn")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db")
    monkeypatch.setenv("TG_MCP_DB_PATH", str(tmp_path/"db.sqlite3"))
    monkeypatch.setenv("WATCHLIST_DEFAULT", "111,222")
    s = load_settings()
    assert s.tg_api_id == 12345
    assert s.sync_interval_min == 15
    assert s.watchlist_default == [111, 222]
    assert s.tg_session_path == tmp_path/"s"
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_config.py -v`
Expected: FAIL (import error).

- [ ] **Step 3: Implement**

```python
# src/tg_mcp/config.py
from pathlib import Path
from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tg_api_id: int
    tg_api_hash: str
    tg_session_path: Path
    anthropic_api_key: str
    summary_model: str = "claude-haiku-5-5"
    notion_token: str
    notion_database_id: str
    tg_mcp_db_path: Path
    sync_interval_min: int = 15
    max_backfill_days: int = 7
    watchlist_default: List[int] = Field(default_factory=list)

    @field_validator("watchlist_default", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v

    @field_validator("tg_session_path", "tg_mcp_db_path")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return Path(str(v)).expanduser()

def load_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/config.py tests/test_config.py
git commit -m "feat(config): typed settings via pydantic-settings

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: SQLite storage layer

**Files:**
- Create: `src/tg_mcp/storage/migrations.py`, `src/tg_mcp/storage/db.py`
- Test: `tests/test_storage_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `connect(path: Path) -> sqlite3.Connection` (WAL, FKs on, Row factory).
  - `migrate(conn) -> None`.
  - `upsert_chat(conn, chat_id: int, title: str, kind: str, watched: bool) -> None`.
  - `set_watched(conn, chat_id: int, watched: bool) -> None`.
  - `list_chats(conn, watched_only: bool=False) -> list[sqlite3.Row]`.
  - `insert_messages(conn, rows: list[dict]) -> int` — returns rows actually inserted (not ignored). Each row has `chat_id, message_id, sender_id, sender_name, date_utc, text, media_kind, reply_to, raw_json`.
  - `get_cursor(conn, chat_id: int) -> int | None`.
  - `set_cursor(conn, chat_id: int, last_message_id: int) -> None`.
  - `messages_for_day(conn, chat_id: int, date: str) -> list[sqlite3.Row]`.
  - `max_message_id_for_day(conn, chat_id: int, date: str) -> int | None`.
  - `get_summary(conn, chat_id: int, date: str) -> sqlite3.Row | None`.
  - `upsert_summary(conn, chat_id: int, date: str, status: str, last_covered_id: int | None, content_json: str | None, notion_page_id: str | None, model: str | None, tokens_in: int | None, tokens_out: int | None, error: str | None) -> None`.
  - `record_run(conn, trigger: str) -> int` and `finish_run(conn, run_id: int, stats_json: str, error: str | None) -> None`.

- [ ] **Step 1: Write migrations DDL**

```python
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
"""
```

- [ ] **Step 2: Write tests**

```python
# tests/test_storage_db.py
import json
from datetime import datetime, timezone
from tg_mcp.storage import db

def _now(): return datetime.now(timezone.utc).isoformat()

def _msg(chat_id, mid, text="hi", date="2026-11-21T10:00:00+00:00"):
    return dict(chat_id=chat_id, message_id=mid, sender_id=1, sender_name="a",
                date_utc=date, text=text, media_kind=None, reply_to=None,
                raw_json=json.dumps({"id": mid}))

def test_migrate_creates_tables(mem_db):
    db.migrate(mem_db)
    tables = {r[0] for r in mem_db.execute("select name from sqlite_master where type='table'")}
    assert {"chats","messages","sync_cursors","summaries","runs"} <= tables

def test_insert_messages_is_idempotent(mem_db):
    db.migrate(mem_db)
    rows = [_msg(1, i) for i in range(5)]
    assert db.insert_messages(mem_db, rows) == 5
    assert db.insert_messages(mem_db, rows) == 0
    assert mem_db.execute("select count(*) from messages").fetchone()[0] == 5

def test_cursor_roundtrip(mem_db):
    db.migrate(mem_db)
    assert db.get_cursor(mem_db, 42) is None
    db.set_cursor(mem_db, 42, 100)
    assert db.get_cursor(mem_db, 42) == 100

def test_messages_for_day_and_max(mem_db):
    db.migrate(mem_db)
    db.insert_messages(mem_db, [
        _msg(1, 1, date="2026-11-21T09:00:00+00:00"),
        _msg(1, 2, date="2026-11-21T22:59:59+00:00"),
        _msg(1, 3, date="2026-11-22T00:00:00+00:00"),
    ])
    rows = db.messages_for_day(mem_db, 1, "2026-11-21")
    assert [r["message_id"] for r in rows] == [1, 2]
    assert db.max_message_id_for_day(mem_db, 1, "2026-11-21") == 2

def test_upsert_summary_overwrites(mem_db):
    db.migrate(mem_db)
    db.upsert_summary(mem_db, 1, "2026-11-21", "pending", None, None, None, None, None, None, None)
    db.upsert_summary(mem_db, 1, "2026-11-21", "done", 42, '{"topics":[]}', "pg", "claude-haiku-5-5", 10, 5, None)
    row = db.get_summary(mem_db, 1, "2026-11-21")
    assert row["status"] == "done" and row["last_covered_id"] == 42

def test_upsert_chat_and_list(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    db.upsert_chat(mem_db, 2, "B", "group", False)
    assert len(db.list_chats(mem_db)) == 2
    assert len(db.list_chats(mem_db, watched_only=True)) == 1
```

- [ ] **Step 3: Run and confirm failure**

Run: `pytest tests/test_storage_db.py -v` → FAIL.

- [ ] **Step 4: Implement `db.py`**

```python
# src/tg_mcp/storage/db.py
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from .migrations import SCHEMA_SQL

def _now() -> str: return datetime.now(timezone.utc).isoformat()

def connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit; we manage txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)

def upsert_chat(conn, chat_id, title, kind, watched) -> None:
    conn.execute(
        """INSERT INTO chats(chat_id,title,kind,watched,added_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, kind=excluded.kind, watched=excluded.watched""",
        (chat_id, title, kind, 1 if watched else 0, _now()))

def set_watched(conn, chat_id, watched) -> None:
    conn.execute("UPDATE chats SET watched=? WHERE chat_id=?", (1 if watched else 0, chat_id))

def list_chats(conn, watched_only=False):
    q = "SELECT * FROM chats" + (" WHERE watched=1" if watched_only else "") + " ORDER BY title"
    return conn.execute(q).fetchall()

def insert_messages(conn, rows) -> int:
    if not rows: return 0
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        before = conn.total_changes
        cur.executemany(
            """INSERT OR IGNORE INTO messages
               (chat_id,message_id,sender_id,sender_name,date_utc,text,media_kind,reply_to,raw_json)
               VALUES (:chat_id,:message_id,:sender_id,:sender_name,:date_utc,:text,:media_kind,:reply_to,:raw_json)""",
            rows)
        inserted = conn.total_changes - before
        cur.execute("COMMIT")
        return inserted
    except Exception:
        cur.execute("ROLLBACK"); raise

def get_cursor(conn, chat_id):
    r = conn.execute("SELECT last_message_id FROM sync_cursors WHERE chat_id=?", (chat_id,)).fetchone()
    return r[0] if r else None

def set_cursor(conn, chat_id, last_message_id) -> None:
    conn.execute(
        """INSERT INTO sync_cursors(chat_id,last_message_id,updated_at) VALUES (?,?,?)
           ON CONFLICT(chat_id) DO UPDATE SET last_message_id=excluded.last_message_id, updated_at=excluded.updated_at""",
        (chat_id, last_message_id, _now()))

def messages_for_day(conn, chat_id, date):
    return conn.execute(
        "SELECT * FROM messages WHERE chat_id=? AND substr(date_utc,1,10)=? ORDER BY message_id",
        (chat_id, date)).fetchall()

def max_message_id_for_day(conn, chat_id, date):
    r = conn.execute(
        "SELECT MAX(message_id) FROM messages WHERE chat_id=? AND substr(date_utc,1,10)=?",
        (chat_id, date)).fetchone()
    return r[0] if r and r[0] is not None else None

def get_summary(conn, chat_id, date):
    return conn.execute("SELECT * FROM summaries WHERE chat_id=? AND date=?", (chat_id, date)).fetchone()

def upsert_summary(conn, chat_id, date, status, last_covered_id, content_json,
                   notion_page_id, model, tokens_in, tokens_out, error) -> None:
    conn.execute(
        """INSERT INTO summaries(chat_id,date,status,last_covered_id,content_json,notion_page_id,
                                 model,tokens_in,tokens_out,error,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(chat_id,date) DO UPDATE SET
             status=excluded.status, last_covered_id=excluded.last_covered_id,
             content_json=excluded.content_json, notion_page_id=excluded.notion_page_id,
             model=excluded.model, tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out,
             error=excluded.error, updated_at=excluded.updated_at""",
        (chat_id, date, status, last_covered_id, content_json, notion_page_id,
         model, tokens_in, tokens_out, error, _now()))

def record_run(conn, trigger) -> int:
    cur = conn.execute("INSERT INTO runs(started_at,trigger) VALUES (?,?)", (_now(), trigger))
    return cur.lastrowid

def finish_run(conn, run_id, stats_json, error) -> None:
    conn.execute("UPDATE runs SET finished_at=?, stats_json=?, error=? WHERE run_id=?",
                 (_now(), stats_json, error, run_id))
```

- [ ] **Step 5: Pass**

Run: `pytest tests/test_storage_db.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tg_mcp/storage/ tests/test_storage_db.py
git commit -m "feat(storage): sqlite schema + idempotent CRUD

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Telegram client wrapper + Protocol

**Files:**
- Create: `src/tg_mcp/telegram/client.py`
- Test: `tests/test_telegram_fake.py`

**Interfaces:**
- Produces:
  - `TelegramPort` Protocol: `async list_chats() -> list[ChatInfo]`, `async iter_new_messages(chat_id: int, min_id: int | None, limit: int=500) -> AsyncIterator[MessageDTO]`, `async send_message(chat_id: int, text: str) -> int`.
  - `ChatInfo` TypedDict: `{chat_id: int, title: str, kind: Literal['group','dm','channel']}`.
  - `MessageDTO` TypedDict matching the `insert_messages` row dict (see Task 2).
  - `TelethonTelegram(settings)` implementing the Protocol.
  - `FakeTelegram(chats, messages)` implementing the Protocol — for tests and demo.

- [ ] **Step 1: Write tests for `FakeTelegram`**

```python
# tests/test_telegram_fake.py
import pytest
from tg_mcp.telegram.client import FakeTelegram

@pytest.mark.asyncio
async def test_fake_list_chats_and_iter():
    fake = FakeTelegram(
        chats=[{"chat_id":1,"title":"Alice","kind":"dm"},
               {"chat_id":2,"title":"Team","kind":"group"}],
        messages={
            1: [{"chat_id":1,"message_id":10,"sender_id":1,"sender_name":"a",
                 "date_utc":"2026-11-21T09:00:00+00:00","text":"hi","media_kind":None,
                 "reply_to":None,"raw_json":"{}"}],
            2: [{"chat_id":2,"message_id":100,"sender_id":2,"sender_name":"b",
                 "date_utc":"2026-11-21T10:00:00+00:00","text":"deploy","media_kind":None,
                 "reply_to":None,"raw_json":"{}"}]})
    assert len(await fake.list_chats()) == 2
    out = [m async for m in fake.iter_new_messages(1, min_id=None)]
    assert len(out) == 1 and out[0]["message_id"] == 10
    out = [m async for m in fake.iter_new_messages(1, min_id=10)]
    assert out == []
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_telegram_fake.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# src/tg_mcp/telegram/client.py
from __future__ import annotations
import json
from pathlib import Path
from typing import AsyncIterator, Literal, Protocol, TypedDict, runtime_checkable

class ChatInfo(TypedDict):
    chat_id: int
    title: str
    kind: Literal["group","dm","channel"]

class MessageDTO(TypedDict):
    chat_id: int
    message_id: int
    sender_id: int | None
    sender_name: str | None
    date_utc: str
    text: str | None
    media_kind: str | None
    reply_to: int | None
    raw_json: str

@runtime_checkable
class TelegramPort(Protocol):
    async def list_chats(self) -> list[ChatInfo]: ...
    async def iter_new_messages(self, chat_id: int, min_id: int | None,
                                limit: int = 500) -> AsyncIterator[MessageDTO]: ...
    async def send_message(self, chat_id: int, text: str) -> int: ...

class FakeTelegram:
    def __init__(self, chats: list[ChatInfo], messages: dict[int, list[MessageDTO]]):
        self._chats = chats
        self._messages = messages
        self.sent: list[tuple[int,str]] = []

    async def list_chats(self) -> list[ChatInfo]:
        return list(self._chats)

    async def iter_new_messages(self, chat_id, min_id, limit=500):
        for m in sorted(self._messages.get(chat_id, []), key=lambda x: x["message_id"]):
            if min_id is not None and m["message_id"] <= min_id:
                continue
            yield m

    async def send_message(self, chat_id, text) -> int:
        self.sent.append((chat_id, text))
        return len(self.sent)

class TelethonTelegram:
    """Thin wrapper over Telethon. Session file path from settings."""
    def __init__(self, api_id: int, api_hash: str, session_path: Path):
        from telethon import TelegramClient
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = TelegramClient(str(session_path), api_id, api_hash)

    async def __aenter__(self):
        await self._client.start()
        try:
            import os
            # best-effort tighten session file perms
            for p in self._client.session.filename, str(self._client.session.filename)+"-journal":
                if p and Path(p).exists():
                    os.chmod(p, 0o600)
        except Exception:
            pass
        return self

    async def __aexit__(self, *exc):
        await self._client.disconnect()

    async def list_chats(self) -> list[ChatInfo]:
        out: list[ChatInfo] = []
        async for d in self._client.iter_dialogs():
            kind = "dm" if d.is_user else ("channel" if getattr(d.entity, "broadcast", False) else "group")
            out.append({"chat_id": int(d.id), "title": d.name or "(no title)", "kind": kind})
        return out

    async def iter_new_messages(self, chat_id, min_id, limit=500):
        kwargs = {"limit": limit, "reverse": True}
        if min_id is not None:
            kwargs["min_id"] = min_id
        async for msg in self._client.iter_messages(chat_id, **kwargs):
            sender = await msg.get_sender() if msg.sender_id else None
            sender_name = None
            if sender is not None:
                sender_name = getattr(sender, "username", None) or \
                              " ".join(x for x in [getattr(sender,"first_name",None),
                                                   getattr(sender,"last_name",None)] if x) or None
            media_kind = type(msg.media).__name__ if msg.media else None
            yield {
                "chat_id": chat_id,
                "message_id": int(msg.id),
                "sender_id": int(msg.sender_id) if msg.sender_id else None,
                "sender_name": sender_name,
                "date_utc": msg.date.isoformat(),
                "text": msg.message or None,
                "media_kind": media_kind,
                "reply_to": int(msg.reply_to_msg_id) if msg.reply_to_msg_id else None,
                "raw_json": json.dumps(msg.to_dict(), default=str),
            }

    async def send_message(self, chat_id, text) -> int:
        m = await self._client.send_message(chat_id, text)
        return int(m.id)
```

- [ ] **Step 4: Pass**

Run: `pytest tests/test_telegram_fake.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/telegram/ tests/test_telegram_fake.py
git commit -m "feat(telegram): Protocol + Telethon wrapper + Fake for tests

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Sync engine

**Files:**
- Create: `src/tg_mcp/sync/engine.py`
- Test: `tests/test_sync_engine.py`

**Interfaces:**
- Consumes: `TelegramPort` (Task 3), `storage.db` (Task 2).
- Produces: `async run_sync(conn, tg: TelegramPort, chat_ids: list[int] | None = None, batch_limit: int = 500) -> dict` returning `{"per_chat": {chat_id: {"inserted": int, "new_cursor": int|None, "error": str|None}}, "total_inserted": int}`. Each chat is processed in its own transaction via `insert_messages`; cursor is updated to the max inserted id (or unchanged if nothing new). If `chat_ids is None`, uses watched chats from `chats` table.

- [ ] **Step 1: Write tests**

```python
# tests/test_sync_engine.py
import pytest
from tg_mcp.storage import db
from tg_mcp.telegram.client import FakeTelegram
from tg_mcp.sync.engine import run_sync

def _m(chat_id, mid, date="2026-11-21T10:00:00+00:00"):
    return {"chat_id":chat_id,"message_id":mid,"sender_id":1,"sender_name":"x",
            "date_utc":date,"text":f"m{mid}","media_kind":None,"reply_to":None,"raw_json":"{}"}

@pytest.mark.asyncio
async def test_first_run_inserts_and_sets_cursor(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    fake = FakeTelegram(
        chats=[{"chat_id":1,"title":"A","kind":"dm"}],
        messages={1:[_m(1,1),_m(1,2),_m(1,3)]})
    res = await run_sync(mem_db, fake)
    assert res["total_inserted"] == 3
    assert db.get_cursor(mem_db, 1) == 3

@pytest.mark.asyncio
async def test_second_run_is_noop(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    fake = FakeTelegram(
        chats=[{"chat_id":1,"title":"A","kind":"dm"}],
        messages={1:[_m(1,1),_m(1,2)]})
    await run_sync(mem_db, fake)
    res2 = await run_sync(mem_db, fake)
    assert res2["total_inserted"] == 0
    assert db.get_cursor(mem_db, 1) == 2

@pytest.mark.asyncio
async def test_incremental_pulls_only_new(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    fake = FakeTelegram(
        chats=[{"chat_id":1,"title":"A","kind":"dm"}],
        messages={1:[_m(1,1),_m(1,2)]})
    await run_sync(mem_db, fake)
    fake._messages[1].extend([_m(1,3),_m(1,4)])
    res = await run_sync(mem_db, fake)
    assert res["total_inserted"] == 2
    assert db.get_cursor(mem_db, 1) == 4

@pytest.mark.asyncio
async def test_unwatched_chats_skipped(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", False)
    fake = FakeTelegram(
        chats=[{"chat_id":1,"title":"A","kind":"dm"}],
        messages={1:[_m(1,1)]})
    res = await run_sync(mem_db, fake)
    assert res["total_inserted"] == 0

@pytest.mark.asyncio
async def test_error_in_one_chat_does_not_block_others(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    db.upsert_chat(mem_db, 2, "B", "group", True)
    class Flaky(FakeTelegram):
        async def iter_new_messages(self, chat_id, min_id, limit=500):
            if chat_id == 1:
                raise RuntimeError("boom")
            async for m in super().iter_new_messages(chat_id, min_id, limit):
                yield m
    fake = Flaky(
        chats=[{"chat_id":1,"title":"A","kind":"dm"},{"chat_id":2,"title":"B","kind":"group"}],
        messages={1:[_m(1,1)], 2:[_m(2,1)]})
    res = await run_sync(mem_db, fake)
    assert res["per_chat"][1]["error"] is not None
    assert res["per_chat"][2]["inserted"] == 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_sync_engine.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# src/tg_mcp/sync/engine.py
from __future__ import annotations
import json
from ..storage import db
from ..telegram.client import TelegramPort

async def run_sync(conn, tg: TelegramPort, chat_ids=None, batch_limit=500) -> dict:
    if chat_ids is None:
        chat_ids = [r["chat_id"] for r in db.list_chats(conn, watched_only=True)]
    per_chat: dict[int, dict] = {}
    total = 0
    for cid in chat_ids:
        cursor = db.get_cursor(conn, cid)
        batch: list[dict] = []
        err = None
        try:
            async for m in tg.iter_new_messages(cid, min_id=cursor, limit=batch_limit):
                batch.append(m)
            inserted = db.insert_messages(conn, batch)
            new_cursor = max((m["message_id"] for m in batch), default=cursor)
            if batch:
                db.set_cursor(conn, cid, new_cursor)
            per_chat[cid] = {"inserted": inserted, "new_cursor": new_cursor, "error": None}
            total += inserted
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            per_chat[cid] = {"inserted": 0, "new_cursor": cursor, "error": err}
    run_id = db.record_run(conn, "manual")
    db.finish_run(conn, run_id, json.dumps(per_chat), None)
    return {"per_chat": per_chat, "total_inserted": total}
```

- [ ] **Step 4: Pass**

Run: `pytest tests/test_sync_engine.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/sync/engine.py tests/test_sync_engine.py
git commit -m "feat(sync): incremental, idempotent, per-chat isolated run_sync

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Noise filter

**Files:**
- Create: `src/tg_mcp/summarize/filter.py`
- Test: `tests/test_noise_filter.py`

**Interfaces:**
- Produces: `filter_messages(rows: list[dict]) -> tuple[list[dict], int]` — returns `(kept_rows, dropped_count)`. Drops: null/empty text with no media, pure-emoji single-token replies like `+`, `👍`, `ok`, `спс`, `thx`, `+1`, `ty`, `done`; service/system messages (`media_kind` in `{"MessageActionChatJoinedByLink","MessageActionPinMessage","MessageActionChatAddUser","MessageActionChatDeleteUser"}`); sticker-only (text is None AND media_kind startswith `"MessageMediaSticker"` or equals `"DocumentAttributeSticker"`).

- [ ] **Step 1: Tests**

```python
# tests/test_noise_filter.py
from tg_mcp.summarize.filter import filter_messages

def _r(**kw):
    base = {"chat_id":1,"message_id":1,"sender_id":1,"sender_name":"a",
            "date_utc":"2026-11-21T10:00:00+00:00","text":None,"media_kind":None,
            "reply_to":None,"raw_json":"{}"}
    base.update(kw); return base

def test_drops_short_acks_and_empty():
    rows = [_r(message_id=1,text="+"), _r(message_id=2,text="ok"),
            _r(message_id=3,text="спс"), _r(message_id=4,text="👍"),
            _r(message_id=5,text=None), _r(message_id=6,text="")]
    kept, dropped = filter_messages(rows)
    assert kept == []
    assert dropped == 6

def test_keeps_substantive():
    rows = [_r(message_id=1,text="deploy зафейлился на миграции"),
            _r(message_id=2,text="")]
    kept, dropped = filter_messages(rows)
    assert len(kept) == 1 and dropped == 1

def test_drops_service_messages():
    rows = [_r(message_id=1,media_kind="MessageActionPinMessage",text=None)]
    kept, _ = filter_messages(rows)
    assert kept == []
```

- [ ] **Step 2: Fail**

Run: `pytest tests/test_noise_filter.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# src/tg_mcp/summarize/filter.py
import re
_ACK = {"+","+1","ok","okay","k","kk","ty","thx","thanks","спс","спасибо","дякую","done","got it"}
_SERVICE = {"MessageActionChatJoinedByLink","MessageActionPinMessage",
            "MessageActionChatAddUser","MessageActionChatDeleteUser"}
_EMOJI = re.compile(
    r"^[\s☀-➿\U0001f300-\U0001faff\U0001f900-\U0001f9ff✀-➿]+$")

def _is_noise(row: dict) -> bool:
    mk = row.get("media_kind") or ""
    if mk in _SERVICE: return True
    if mk.startswith("MessageMediaSticker"): return True
    text = (row.get("text") or "").strip()
    if not text and not mk: return True
    if not text: return False  # has non-sticker media, keep
    low = text.lower()
    if low in _ACK: return True
    if len(text) <= 3 and _EMOJI.match(text): return True
    if _EMOJI.match(text) and len(text) <= 8: return True
    return False

def filter_messages(rows):
    kept = [r for r in rows if not _is_noise(r)]
    return kept, len(rows) - len(kept)
```

- [ ] **Step 4: Pass**

Run: `pytest tests/test_noise_filter.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/summarize/filter.py tests/test_noise_filter.py
git commit -m "feat(summarize): heuristic noise filter with tests

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Notion sink

**Files:**
- Create: `src/tg_mcp/storage/notion.py`
- Test: `tests/test_notion_sink.py`

**Interfaces:**
- Consumes: `httpx.AsyncClient`.
- Produces:
  - `NotionSink(token: str, database_id: str, http: httpx.AsyncClient | None = None)`.
  - `async upsert_summary_page(chat_id: int, chat_title: str, date: str, summary: dict, message_count: int) -> str` — returns Notion page id. Finds existing page via DB query on composite property `Key` (= `f"{chat_id}:{date}"`). If found → PATCH (update blocks + props). If not → POST create.
  - Blocks: heading with chat+date; callout with counts; sections for Topics / Decisions / Action Items / Open Questions / Links / Participants.

- [ ] **Step 1: Tests with respx (install `respx` in dev deps if missing — but to avoid extra deps, we mock `httpx.AsyncClient` directly via `MockTransport`).**

```python
# tests/test_notion_sink.py
import httpx, pytest, json
from tg_mcp.storage.notion import NotionSink

QUERY_URL_SUFFIX = "/databases/db1/query"

@pytest.mark.asyncio
async def test_creates_when_missing():
    calls = []
    async def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.url.path.endswith(QUERY_URL_SUFFIX):
            return httpx.Response(200, json={"results": []})
        if req.url.path == "/v1/pages" and req.method == "POST":
            return httpx.Response(200, json={"id": "new-page-id"})
        return httpx.Response(500)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.notion.com")
    sink = NotionSink("tkn", "db1", http=client)
    pid = await sink.upsert_summary_page(1, "Alice", "2026-11-21",
                                         {"topics":[{"title":"t","summary":"s"}]}, 5)
    assert pid == "new-page-id"
    assert ("POST","/v1/pages") in calls

@pytest.mark.asyncio
async def test_updates_when_found():
    async def handler(req):
        if req.url.path.endswith(QUERY_URL_SUFFIX):
            return httpx.Response(200, json={"results":[{"id":"existing-id"}]})
        if req.url.path == "/v1/blocks/existing-id/children" and req.method == "GET":
            return httpx.Response(200, json={"results":[], "has_more": False})
        if req.url.path == "/v1/blocks/existing-id/children" and req.method == "PATCH":
            return httpx.Response(200, json={})
        if req.url.path == "/v1/pages/existing-id" and req.method == "PATCH":
            return httpx.Response(200, json={"id":"existing-id"})
        return httpx.Response(500, text=req.url.path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.notion.com")
    sink = NotionSink("tkn", "db1", http=client)
    pid = await sink.upsert_summary_page(1, "Alice", "2026-11-21", {"topics":[]}, 0)
    assert pid == "existing-id"
```

- [ ] **Step 2: Fail**

Run: `pytest tests/test_notion_sink.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# src/tg_mcp/storage/notion.py
from __future__ import annotations
import httpx

NOTION_VERSION = "2022-06-28"

def _para(text: str) -> dict:
    return {"object":"block","type":"paragraph",
            "paragraph":{"rich_text":[{"type":"text","text":{"content":text[:1900]}}]}}

def _h2(text: str) -> dict:
    return {"object":"block","type":"heading_2",
            "heading_2":{"rich_text":[{"type":"text","text":{"content":text}}]}}

def _bullet(text: str) -> dict:
    return {"object":"block","type":"bulleted_list_item",
            "bulleted_list_item":{"rich_text":[{"type":"text","text":{"content":text[:1900]}}]}}

def render_blocks(summary: dict, message_count: int) -> list[dict]:
    blocks: list[dict] = [_para(f"Messages considered: {message_count}")]
    for section, key, formatter in [
        ("Topics","topics", lambda t: f"{t.get('title','')}: {t.get('summary','')}"),
        ("Decisions","decisions", str),
        ("Action items","action_items",
            lambda a: f"[{a.get('owner','?')}] {a.get('text','')}"),
        ("Open questions","open_questions", str),
        ("Links","links", lambda l: f"{l.get('url','')} — {l.get('context','')}"),
    ]:
        items = summary.get(key) or []
        if not items: continue
        blocks.append(_h2(section))
        for it in items:
            blocks.append(_bullet(formatter(it) if not isinstance(it, str) else it))
    ps = summary.get("participants") or []
    if ps:
        blocks.append(_h2("Participants"))
        blocks.append(_para(", ".join(ps)))
    return blocks

class NotionSink:
    def __init__(self, token: str, database_id: str, http: httpx.AsyncClient | None = None):
        self._db = database_id
        self._own = http is None
        self._http = http or httpx.AsyncClient(base_url="https://api.notion.com", timeout=30)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def close(self):
        if self._own:
            await self._http.aclose()

    async def _find_page(self, key: str) -> str | None:
        r = await self._http.post(f"/v1/databases/{self._db}/query",
            headers=self._headers,
            json={"filter":{"property":"Key","rich_text":{"equals": key}}})
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0]["id"] if results else None

    async def _clear_children(self, page_id: str) -> None:
        # Fetch and delete existing children so update is idempotent
        r = await self._http.get(f"/v1/blocks/{page_id}/children",
                                  headers=self._headers, params={"page_size": 100})
        r.raise_for_status()
        for b in r.json().get("results", []):
            await self._http.request("DELETE", f"/v1/blocks/{b['id']}", headers=self._headers)

    async def upsert_summary_page(self, chat_id, chat_title, date, summary, message_count) -> str:
        key = f"{chat_id}:{date}"
        title = f"{chat_title} — {date}"
        blocks = render_blocks(summary, message_count)
        props = {
            "Name":{"title":[{"text":{"content": title}}]},
            "Key":{"rich_text":[{"text":{"content": key}}]},
            "Date":{"date":{"start": date}},
            "ChatId":{"number": chat_id},
            "MessageCount":{"number": message_count},
        }
        existing = await self._find_page(key)
        if existing:
            r = await self._http.patch(f"/v1/pages/{existing}",
                                        headers=self._headers, json={"properties": props})
            r.raise_for_status()
            await self._clear_children(existing)
            r = await self._http.patch(f"/v1/blocks/{existing}/children",
                                        headers=self._headers, json={"children": blocks})
            r.raise_for_status()
            return existing
        r = await self._http.post("/v1/pages", headers=self._headers,
                                   json={"parent":{"database_id": self._db},
                                         "properties": props, "children": blocks})
        r.raise_for_status()
        return r.json()["id"]
```

- [ ] **Step 4: Pass**

Run: `pytest tests/test_notion_sink.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/storage/notion.py tests/test_notion_sink.py
git commit -m "feat(notion): idempotent per-(chat,date) page upsert

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Prompts + Claude client

**Files:**
- Create: `src/tg_mcp/summarize/prompts.py`, `src/tg_mcp/summarize/claude.py`
- Test: `tests/test_claude_client.py`

**Interfaces:**
- Produces:
  - `SYSTEM_PROMPT: str` and `build_user_prompt(chat_title: str, date: str, messages: list[dict]) -> str` — formats messages as `[time] sender: text` lines.
  - `ClaudePort` Protocol: `async summarize(chat_title, date, messages) -> tuple[dict, int, int]` returning `(summary_json, tokens_in, tokens_out)`.
  - `FakeClaude(response: dict)` for tests.
  - `AnthropicClaude(api_key: str, model: str)` implementing the Protocol — calls `anthropic.AsyncAnthropic`. Uses `response_format` / tool-call style to force JSON matching the summary schema (see spec §3.7).

- [ ] **Step 1: Tests (fake only; no live API)**

```python
# tests/test_claude_client.py
import pytest
from tg_mcp.summarize.claude import FakeClaude
from tg_mcp.summarize.prompts import build_user_prompt, SYSTEM_PROMPT

def test_build_user_prompt_formats_lines():
    msgs = [{"date_utc":"2026-11-21T09:00:00+00:00","sender_name":"a","text":"hi"},
            {"date_utc":"2026-11-21T09:05:00+00:00","sender_name":"b","text":"hello"}]
    p = build_user_prompt("Team", "2026-11-21", msgs)
    assert "Team" in p and "2026-11-21" in p and "a: hi" in p and "b: hello" in p

def test_system_prompt_mentions_json_fields():
    for k in ["topics","decisions","action_items","open_questions","links","participants"]:
        assert k in SYSTEM_PROMPT

@pytest.mark.asyncio
async def test_fake_claude_returns_canned():
    fake = FakeClaude({"topics":[{"title":"t","summary":"s"}],
                       "decisions":[],"action_items":[],"open_questions":[],
                       "links":[],"participants":["a"]})
    out, tin, tout = await fake.summarize("Team","2026-11-21",[{"text":"x"}])
    assert out["topics"][0]["title"] == "t"
    assert tin > 0 and tout > 0
```

- [ ] **Step 2: Fail**

Run: `pytest tests/test_claude_client.py -v` → FAIL.

- [ ] **Step 3: Implement prompts**

```python
# src/tg_mcp/summarize/prompts.py
SYSTEM_PROMPT = """You are a terse work-chat summarizer.
Given one day of messages from a single chat, output STRICT JSON with keys:
topics (list of {title, summary}), decisions (list of strings),
action_items (list of {text, owner}), open_questions (list of strings),
links (list of {url, context}), participants (list of names).
Ignore chit-chat, stickers, +1s, thanks, and small talk.
Deduplicate repeated points. Keep each topic summary to 1-2 sentences.
If the day has nothing substantive, return all empty lists with topics=[].
Language: write summaries in the dominant language of the messages (ru/uk/en)."""

def build_user_prompt(chat_title: str, date: str, messages: list[dict]) -> str:
    lines = [f"Chat: {chat_title}", f"Date (UTC): {date}", "Messages:"]
    for m in messages:
        t = (m.get("date_utc") or "")[11:16]
        who = m.get("sender_name") or "?"
        txt = (m.get("text") or "").replace("\n"," ").strip()
        if not txt: continue
        lines.append(f"[{t}] {who}: {txt}")
    return "\n".join(lines)
```

- [ ] **Step 4: Implement claude client**

```python
# src/tg_mcp/summarize/claude.py
from __future__ import annotations
import json
from typing import Protocol, runtime_checkable
from .prompts import SYSTEM_PROMPT, build_user_prompt

@runtime_checkable
class ClaudePort(Protocol):
    async def summarize(self, chat_title: str, date: str,
                        messages: list[dict]) -> tuple[dict, int, int]: ...

class FakeClaude:
    def __init__(self, response: dict): self._r = response
    async def summarize(self, chat_title, date, messages):
        payload = build_user_prompt(chat_title, date, messages)
        return self._r, len(payload), len(json.dumps(self._r))

class AnthropicClaude:
    def __init__(self, api_key: str, model: str):
        from anthropic import AsyncAnthropic
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def summarize(self, chat_title, date, messages):
        prompt = build_user_prompt(chat_title, date, messages)
        resp = await self._client.messages.create(
            model=self._model, max_tokens=2000, system=SYSTEM_PROMPT,
            messages=[{"role":"user","content": prompt + "\n\nReturn ONLY the JSON object."}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{"); end = text.rfind("}")
            data = json.loads(text[start:end+1]) if start >= 0 and end > start else {
                "topics":[], "decisions":[], "action_items":[],
                "open_questions":[], "links":[], "participants":[]}
        u = resp.usage
        return data, int(u.input_tokens), int(u.output_tokens)
```

- [ ] **Step 5: Pass**

Run: `pytest tests/test_claude_client.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tg_mcp/summarize/prompts.py src/tg_mcp/summarize/claude.py tests/test_claude_client.py
git commit -m "feat(summarize): prompts + anthropic client + fake

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Summarize pipeline

**Files:**
- Create: `src/tg_mcp/summarize/pipeline.py`
- Test: `tests/test_summary_pipeline.py`

**Interfaces:**
- Consumes: `storage.db`, `filter_messages` (Task 5), `ClaudePort` (Task 7), `NotionSink` (Task 6).
- Produces:
  - `async summarize_day(conn, chat_id: int, date: str, claude: ClaudePort, notion: NotionSink | None, model_id: str) -> dict` — pulls messages for the day, applies filter, calls claude, upserts summary row, upserts notion page if sink provided. Returns the stored summary dict.
  - `async run_pending_summaries(conn, claude, notion, model_id, dates: list[str]) -> dict[tuple[int,str], str]` — for each (watched chat, date) where `max(messages.id) > summaries.last_covered_id` OR no summary row exists, calls `summarize_day`. Returns a map of `(chat_id, date) -> status`.

- [ ] **Step 1: Tests**

```python
# tests/test_summary_pipeline.py
import json, pytest
from tg_mcp.storage import db
from tg_mcp.summarize.claude import FakeClaude
from tg_mcp.summarize.pipeline import summarize_day, run_pending_summaries

def _m(chat_id, mid, text, date="2026-11-21T10:00:00+00:00"):
    return {"chat_id":chat_id,"message_id":mid,"sender_id":1,"sender_name":"a",
            "date_utc":date,"text":text,"media_kind":None,"reply_to":None,"raw_json":"{}"}

@pytest.mark.asyncio
async def test_summarize_day_writes_row(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    db.insert_messages(mem_db, [_m(1,1,"deploy fix"),_m(1,2,"+"),_m(1,3,"rolled back")])
    fake = FakeClaude({"topics":[{"title":"deploy","summary":"fixed and rolled back"}],
                       "decisions":["rollback"],"action_items":[],"open_questions":[],
                       "links":[],"participants":["a"]})
    out = await summarize_day(mem_db, 1, "2026-11-21", fake, None, "claude-haiku-5-5")
    assert out["decisions"] == ["rollback"]
    row = db.get_summary(mem_db, 1, "2026-11-21")
    assert row["status"] == "done" and row["last_covered_id"] == 3
    assert row["model"] == "claude-haiku-5-5"

@pytest.mark.asyncio
async def test_rerun_without_new_messages_is_skipped(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    db.insert_messages(mem_db, [_m(1,1,"x")])
    fake = FakeClaude({"topics":[],"decisions":[],"action_items":[],
                       "open_questions":[],"links":[],"participants":[]})
    await summarize_day(mem_db, 1, "2026-11-21", fake, None, "claude-haiku-5-5")
    res = await run_pending_summaries(mem_db, fake, None, "claude-haiku-5-5", ["2026-11-21"])
    assert res == {}

@pytest.mark.asyncio
async def test_rerun_with_new_messages_resummarizes(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    db.insert_messages(mem_db, [_m(1,1,"x")])
    fake = FakeClaude({"topics":[{"title":"t","summary":"s"}],"decisions":[],
                       "action_items":[],"open_questions":[],"links":[],"participants":[]})
    await summarize_day(mem_db, 1, "2026-11-21", fake, None, "claude-haiku-5-5")
    db.insert_messages(mem_db, [_m(1,2,"new stuff")])
    res = await run_pending_summaries(mem_db, fake, None, "claude-haiku-5-5", ["2026-11-21"])
    assert res == {(1, "2026-11-21"): "done"}
    assert db.get_summary(mem_db, 1, "2026-11-21")["last_covered_id"] == 2
```

- [ ] **Step 2: Fail**

Run: `pytest tests/test_summary_pipeline.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# src/tg_mcp/summarize/pipeline.py
from __future__ import annotations
import json
from ..storage import db
from .filter import filter_messages

async def summarize_day(conn, chat_id, date, claude, notion, model_id) -> dict:
    rows = [dict(r) for r in db.messages_for_day(conn, chat_id, date)]
    kept, dropped = filter_messages(rows)
    chat = conn.execute("SELECT title FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
    title = chat["title"] if chat else str(chat_id)
    last_id = max((r["message_id"] for r in rows), default=None)
    if not kept:
        summary = {"topics":[],"decisions":[],"action_items":[],
                   "open_questions":[],"links":[],"participants":[],
                   "message_count":len(rows),"noise_filtered_count":dropped}
        db.upsert_summary(conn, chat_id, date, "done", last_id,
                          json.dumps(summary), None, model_id, 0, 0, None)
        return summary
    try:
        data, tin, tout = await claude.summarize(title, date, kept)
        data["message_count"] = len(rows)
        data["noise_filtered_count"] = dropped
        page_id = None
        if notion is not None:
            page_id = await notion.upsert_summary_page(chat_id, title, date, data, len(rows))
        db.upsert_summary(conn, chat_id, date, "done", last_id,
                          json.dumps(data), page_id, model_id, tin, tout, None)
        return data
    except Exception as e:
        db.upsert_summary(conn, chat_id, date, "error", last_id, None, None,
                          model_id, None, None, f"{type(e).__name__}: {e}")
        raise

async def run_pending_summaries(conn, claude, notion, model_id, dates) -> dict:
    out: dict = {}
    for cid in [r["chat_id"] for r in db.list_chats(conn, watched_only=True)]:
        for d in dates:
            max_id = db.max_message_id_for_day(conn, cid, d)
            if max_id is None: continue
            existing = db.get_summary(conn, cid, d)
            if existing and existing["status"] == "done" and (existing["last_covered_id"] or 0) >= max_id:
                continue
            try:
                await summarize_day(conn, cid, d, claude, notion, model_id)
                out[(cid, d)] = "done"
            except Exception as e:
                out[(cid, d)] = f"error:{e}"
    return out
```

- [ ] **Step 4: Pass**

Run: `pytest tests/test_summary_pipeline.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/summarize/pipeline.py tests/test_summary_pipeline.py
git commit -m "feat(summarize): pipeline with idempotent (chat,date) summaries

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: MCP server + tools

**Files:**
- Create: `src/tg_mcp/tools/handlers.py`, `src/tg_mcp/server.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `AppContext` dataclass: `conn, tg: TelegramPort, claude: ClaudePort, notion: NotionSink | None, settings: Settings`.
  - Pure async handler functions (callable without the MCP transport layer so they can be unit-tested):
    - `async list_chats_tool(ctx, kind: str = "all", limit: int = 100) -> list[dict]`
    - `async fetch_messages_tool(ctx, chat_id: int, limit: int = 50, before_id: int | None = None) -> list[dict]`
    - `async send_message_tool(ctx, chat_id: int, text: str) -> dict`
    - `async sync_now_tool(ctx, chat_ids: list[int] | None = None) -> dict`
    - `async get_summary_tool(ctx, chat_id: int, date: str) -> dict | None`
    - `async list_summaries_tool(ctx, date_from: str, date_to: str, chat_ids: list[int] | None = None) -> list[dict]`
    - `async set_watchlist_tool(ctx, chat_ids: list[int], mode: str = "replace") -> dict`
    - `async get_status_tool(ctx) -> dict`
  - `main()` in `server.py` wires `mcp.server.stdio` + registers each tool.

- [ ] **Step 1: Tests for handlers (no MCP transport)**

```python
# tests/test_mcp_tools.py
import pytest
from dataclasses import dataclass
from tg_mcp.storage import db
from tg_mcp.telegram.client import FakeTelegram
from tg_mcp.summarize.claude import FakeClaude
from tg_mcp.tools.handlers import (
    AppContext, list_chats_tool, send_message_tool, sync_now_tool,
    set_watchlist_tool, get_status_tool, list_summaries_tool
)

def _ctx(conn, tg):
    claude = FakeClaude({"topics":[],"decisions":[],"action_items":[],
                         "open_questions":[],"links":[],"participants":[]})
    return AppContext(conn=conn, tg=tg, claude=claude, notion=None, settings=None)

@pytest.mark.asyncio
async def test_set_watchlist_and_sync(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", False)
    tg = FakeTelegram(
        chats=[{"chat_id":1,"title":"A","kind":"dm"}],
        messages={1:[{"chat_id":1,"message_id":1,"sender_id":1,"sender_name":"x",
                      "date_utc":"2026-11-21T10:00:00+00:00","text":"hi",
                      "media_kind":None,"reply_to":None,"raw_json":"{}"}]})
    ctx = _ctx(mem_db, tg)
    await set_watchlist_tool(ctx, [1], "replace")
    out = await sync_now_tool(ctx)
    assert out["total_inserted"] == 1

@pytest.mark.asyncio
async def test_list_chats_filters_by_kind(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    db.upsert_chat(mem_db, 2, "B", "group", True)
    ctx = _ctx(mem_db, FakeTelegram([], {}))
    out = await list_chats_tool(ctx, kind="dm")
    assert [c["chat_id"] for c in out] == [1]

@pytest.mark.asyncio
async def test_send_message(mem_db):
    tg = FakeTelegram([], {})
    ctx = _ctx(mem_db, tg)
    out = await send_message_tool(ctx, 42, "hello")
    assert tg.sent == [(42, "hello")]
    assert out["message_id"] >= 1
```

- [ ] **Step 2: Fail**

Run: `pytest tests/test_mcp_tools.py -v` → FAIL.

- [ ] **Step 3: Implement handlers**

```python
# src/tg_mcp/tools/handlers.py
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any
from ..storage import db
from ..sync.engine import run_sync

@dataclass
class AppContext:
    conn: Any
    tg: Any
    claude: Any
    notion: Any
    settings: Any

def _row(r): return {k: r[k] for k in r.keys()}

async def list_chats_tool(ctx, kind: str = "all", limit: int = 100):
    rows = db.list_chats(ctx.conn)
    if kind != "all":
        rows = [r for r in rows if r["kind"] == kind]
    return [_row(r) for r in rows[:limit]]

async def fetch_messages_tool(ctx, chat_id: int, limit: int = 50, before_id: int | None = None):
    q = "SELECT * FROM messages WHERE chat_id=?"
    args: list = [chat_id]
    if before_id is not None:
        q += " AND message_id < ?"; args.append(before_id)
    q += " ORDER BY message_id DESC LIMIT ?"; args.append(limit)
    return [_row(r) for r in ctx.conn.execute(q, args).fetchall()]

async def send_message_tool(ctx, chat_id: int, text: str) -> dict:
    mid = await ctx.tg.send_message(chat_id, text)
    return {"chat_id": chat_id, "message_id": mid}

async def sync_now_tool(ctx, chat_ids: list[int] | None = None) -> dict:
    return await run_sync(ctx.conn, ctx.tg, chat_ids=chat_ids)

async def get_summary_tool(ctx, chat_id: int, date: str):
    row = db.get_summary(ctx.conn, chat_id, date)
    return _row(row) if row else None

async def list_summaries_tool(ctx, date_from: str, date_to: str, chat_ids: list[int] | None = None):
    q = "SELECT * FROM summaries WHERE date BETWEEN ? AND ?"
    args: list = [date_from, date_to]
    if chat_ids:
        q += " AND chat_id IN (" + ",".join("?"*len(chat_ids)) + ")"
        args.extend(chat_ids)
    q += " ORDER BY date DESC, chat_id"
    return [_row(r) for r in ctx.conn.execute(q, args).fetchall()]

async def set_watchlist_tool(ctx, chat_ids: list[int], mode: str = "replace") -> dict:
    # Ensure chats exist; if not, hydrate from Telegram.
    existing = {r["chat_id"]: r for r in db.list_chats(ctx.conn)}
    needed = [c for c in chat_ids if c not in existing]
    if needed:
        for info in await ctx.tg.list_chats():
            if info["chat_id"] in needed:
                db.upsert_chat(ctx.conn, info["chat_id"], info["title"], info["kind"], False)
    if mode == "replace":
        for r in db.list_chats(ctx.conn):
            db.set_watched(ctx.conn, r["chat_id"], r["chat_id"] in chat_ids)
    elif mode == "add":
        for c in chat_ids: db.set_watched(ctx.conn, c, True)
    elif mode == "remove":
        for c in chat_ids: db.set_watched(ctx.conn, c, False)
    else:
        raise ValueError(f"bad mode: {mode}")
    watched = [r["chat_id"] for r in db.list_chats(ctx.conn, watched_only=True)]
    return {"watched": watched}

async def get_status_tool(ctx) -> dict:
    last_run = ctx.conn.execute(
        "SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    cursors = {r["chat_id"]: r["last_message_id"]
               for r in ctx.conn.execute("SELECT * FROM sync_cursors").fetchall()}
    pending = ctx.conn.execute(
        "SELECT chat_id,date,status FROM summaries WHERE status!='done' ORDER BY date DESC LIMIT 20").fetchall()
    return {
        "last_run": _row(last_run) if last_run else None,
        "cursors": cursors,
        "pending_summaries": [_row(r) for r in pending],
    }
```

- [ ] **Step 4: Pass handler tests**

Run: `pytest tests/test_mcp_tools.py -v` → PASS.

- [ ] **Step 5: Implement MCP server (stdio)**

```python
# src/tg_mcp/server.py
from __future__ import annotations
import asyncio, json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from .config import load_settings
from .storage import db
from .storage.notion import NotionSink
from .telegram.client import TelethonTelegram
from .summarize.claude import AnthropicClaude
from .tools.handlers import (
    AppContext, list_chats_tool, fetch_messages_tool, send_message_tool,
    sync_now_tool, get_summary_tool, list_summaries_tool,
    set_watchlist_tool, get_status_tool,
)

TOOL_DEFS = [
    Tool(name="list_chats", description="List known chats",
         inputSchema={"type":"object","properties":{
             "kind":{"type":"string","enum":["all","dm","group","channel"],"default":"all"},
             "limit":{"type":"integer","default":100}}}),
    Tool(name="fetch_messages", description="Read raw messages from local store",
         inputSchema={"type":"object","required":["chat_id"],"properties":{
             "chat_id":{"type":"integer"}, "limit":{"type":"integer","default":50},
             "before_id":{"type":["integer","null"]}}}),
    Tool(name="send_message", description="Send a Telegram message",
         inputSchema={"type":"object","required":["chat_id","text"],"properties":{
             "chat_id":{"type":"integer"}, "text":{"type":"string"}}}),
    Tool(name="sync_now", description="Run an incremental sync now",
         inputSchema={"type":"object","properties":{
             "chat_ids":{"type":["array","null"],"items":{"type":"integer"}}}}),
    Tool(name="get_summary", description="Get one day's summary for a chat",
         inputSchema={"type":"object","required":["chat_id","date"],"properties":{
             "chat_id":{"type":"integer"}, "date":{"type":"string"}}}),
    Tool(name="list_summaries", description="List summaries in a date range",
         inputSchema={"type":"object","required":["date_from","date_to"],"properties":{
             "date_from":{"type":"string"}, "date_to":{"type":"string"},
             "chat_ids":{"type":["array","null"],"items":{"type":"integer"}}}}),
    Tool(name="set_watchlist", description="Replace/add/remove watched chats",
         inputSchema={"type":"object","required":["chat_ids"],"properties":{
             "chat_ids":{"type":"array","items":{"type":"integer"}},
             "mode":{"type":"string","enum":["replace","add","remove"],"default":"replace"}}}),
    Tool(name="get_status", description="Last run, cursors, pending summaries",
         inputSchema={"type":"object","properties":{}}),
]

def _text(obj) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(obj, ensure_ascii=False, default=str))]

async def _amain():
    settings = load_settings()
    conn = db.connect(settings.tg_mcp_db_path); db.migrate(conn)
    for cid in settings.watchlist_default:
        db.upsert_chat(conn, cid, f"chat:{cid}", "group", True)

    async with TelethonTelegram(settings.tg_api_id, settings.tg_api_hash,
                                 settings.tg_session_path) as tg:
        notion = NotionSink(settings.notion_token, settings.notion_database_id)
        claude = AnthropicClaude(settings.anthropic_api_key, settings.summary_model)
        ctx = AppContext(conn=conn, tg=tg, claude=claude, notion=notion, settings=settings)
        server = Server("tg-mcp")

        @server.list_tools()
        async def _lt(): return TOOL_DEFS

        @server.call_tool()
        async def _ct(name: str, arguments: dict):
            fn = {
                "list_chats": list_chats_tool, "fetch_messages": fetch_messages_tool,
                "send_message": send_message_tool, "sync_now": sync_now_tool,
                "get_summary": get_summary_tool, "list_summaries": list_summaries_tool,
                "set_watchlist": set_watchlist_tool, "get_status": get_status_tool,
            }[name]
            return _text(await fn(ctx, **(arguments or {})))

        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())
        await notion.close()

def main():
    asyncio.run(_amain())

if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Smoke import**

Run: `python -c "import tg_mcp.server"` → no error.

- [ ] **Step 7: Commit**

```bash
git add src/tg_mcp/tools/ src/tg_mcp/server.py tests/test_mcp_tools.py
git commit -m "feat(mcp): stdio server + 8 tools wired to handlers

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: Scheduler

**Files:**
- Create: `src/tg_mcp/sync/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `start_scheduler(ctx: AppContext) -> AsyncIOScheduler` that schedules `run_sync` every `settings.sync_interval_min` minutes and `run_pending_summaries` right after. Exposes `tick(ctx)` async for direct call in tests (same body as the scheduled job).

- [ ] **Step 1: Tests**

```python
# tests/test_scheduler.py
import pytest
from dataclasses import dataclass
from tg_mcp.storage import db
from tg_mcp.telegram.client import FakeTelegram
from tg_mcp.summarize.claude import FakeClaude
from tg_mcp.tools.handlers import AppContext
from tg_mcp.sync.scheduler import tick

class _S:
    sync_interval_min = 15
    summary_model = "claude-haiku-5-5"

@pytest.mark.asyncio
async def test_tick_runs_sync_and_summaries(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    tg = FakeTelegram(
        chats=[{"chat_id":1,"title":"A","kind":"dm"}],
        messages={1:[{"chat_id":1,"message_id":1,"sender_id":1,"sender_name":"x",
                      "date_utc":"2026-11-21T10:00:00+00:00","text":"deploy",
                      "media_kind":None,"reply_to":None,"raw_json":"{}"}]})
    claude = FakeClaude({"topics":[{"title":"t","summary":"s"}],
                         "decisions":[],"action_items":[],"open_questions":[],
                         "links":[],"participants":[]})
    ctx = AppContext(conn=mem_db, tg=tg, claude=claude, notion=None, settings=_S())
    report = await tick(ctx, dates=["2026-11-21"])
    assert report["sync"]["total_inserted"] == 1
    assert report["summaries"][(1,"2026-11-21")] == "done"
```

- [ ] **Step 2: Fail**

Run: `pytest tests/test_scheduler.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# src/tg_mcp/sync/scheduler.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .engine import run_sync
from ..summarize.pipeline import run_pending_summaries

def _today_and_yesterday() -> list[str]:
    now = datetime.now(timezone.utc).date()
    return [now.isoformat(), (now - timedelta(days=1)).isoformat()]

async def tick(ctx, dates: list[str] | None = None) -> dict:
    dates = dates or _today_and_yesterday()
    sync_report = await run_sync(ctx.conn, ctx.tg)
    summaries = await run_pending_summaries(
        ctx.conn, ctx.claude, ctx.notion, ctx.settings.summary_model, dates)
    return {"sync": sync_report, "summaries": summaries}

def start_scheduler(ctx) -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(tick, "interval",
                  minutes=ctx.settings.sync_interval_min,
                  args=[ctx], coalesce=True, max_instances=1,
                  next_run_time=datetime.now(timezone.utc))
    sched.start()
    return sched
```

- [ ] **Step 4: Pass**

Run: `pytest tests/test_scheduler.py -v` → PASS.

- [ ] **Step 5: Wire scheduler into server**

Edit `src/tg_mcp/server.py`: after creating `ctx`, add `sched = start_scheduler(ctx)`; on shutdown call `sched.shutdown(wait=False)`. Import at top: `from .sync.scheduler import start_scheduler`.

- [ ] **Step 6: Commit**

```bash
git add src/tg_mcp/sync/scheduler.py src/tg_mcp/server.py tests/test_scheduler.py
git commit -m "feat(scheduler): periodic sync+summaries wired into server

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: Login CLI

**Files:**
- Create: `src/tg_mcp/login.py`

**Interfaces:**
- Produces: `main()` entrypoint `tg-mcp-login` that prompts for phone/code once and writes the session file at `settings.tg_session_path`. No tests (interactive).

- [ ] **Step 1: Implement**

```python
# src/tg_mcp/login.py
import asyncio, os
from .config import load_settings
from .telegram.client import TelethonTelegram

async def _amain():
    s = load_settings()
    async with TelethonTelegram(s.tg_api_id, s.tg_api_hash, s.tg_session_path):
        pass
    try:
        os.chmod(s.tg_session_path.with_suffix(".session"), 0o600)
    except FileNotFoundError:
        pass
    print(f"Session saved to {s.tg_session_path}")

def main(): asyncio.run(_amain())

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke import**

Run: `python -c "import tg_mcp.login"` → no error.

- [ ] **Step 3: Commit**

```bash
git add src/tg_mcp/login.py
git commit -m "feat(login): one-off Telegram session CLI

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 12: README + demo script

**Files:**
- Create: `README.md`, `scripts/demo.sh`

- [ ] **Step 1: Write README**

Sections, in order:
1. **What it is** (one paragraph; name the spec file path).
2. **Setup** (exact commands):
   ```bash
   python3.11 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   cp .env.example .env   # fill it in
   tg-mcp-login           # interactive, once
   ```
3. **Running** — stdio under Claude Desktop/Code via `claude_desktop_config.json`:
   ```json
   {"mcpServers":{"tg-mcp":{"command":"tg-mcp","env":{"...": "..."}}}}
   ```
4. **Data flow diagram** (ASCII, same as spec §3.2).
5. **MCP tools reference** — the table from spec §3.3.
6. **Design decisions** (short) — link to the spec.
7. **Trade-offs** — bullets from spec §7.
8. **Limits** — bullets from spec §8.
9. **Demo**:
   ```bash
   ./scripts/demo.sh
   ```
   describe that the script runs `sync_now` twice and prints that the second pass inserts 0 rows (no duplicates).

- [ ] **Step 2: Write `scripts/demo.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
python - <<'PY'
import asyncio, os, json, sqlite3
from tg_mcp.config import load_settings
from tg_mcp.storage import db
from tg_mcp.telegram.client import TelethonTelegram
from tg_mcp.sync.engine import run_sync

async def main():
    s = load_settings()
    conn = db.connect(s.tg_mcp_db_path); db.migrate(conn)
    async with TelethonTelegram(s.tg_api_id, s.tg_api_hash, s.tg_session_path) as tg:
        for cid in s.watchlist_default:
            db.upsert_chat(conn, cid, f"chat:{cid}", "group", True)
        r1 = await run_sync(conn, tg)
        r2 = await run_sync(conn, tg)
    print(json.dumps({"first_run": r1["total_inserted"], "second_run": r2["total_inserted"]}))
    assert r2["total_inserted"] == 0, "second run must insert 0 (idempotency)"
asyncio.run(main())
PY
```

Mark executable: `chmod +x scripts/demo.sh`.

- [ ] **Step 3: Commit**

```bash
git add README.md scripts/demo.sh
git commit -m "docs: README with setup, data flow, tools, trade-offs, demo

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- MCP server (stdio) + 8 tools — Task 9. ✓
- Incremental sync, groups + DM, idempotent — Tasks 2, 3, 4. ✓
- Scheduled + on-demand sync — Task 10 + `sync_now` in Task 9. ✓
- Daily LLM summaries, noise filter — Tasks 5, 7, 8. ✓
- Notion storage — Task 6 + 8. ✓
- Secrets, session file 0600, no-logging — Task 1 (`.env`), Task 3 (chmod), Task 11. ✓
- README + demo + repeat-run proof — Task 12. ✓
- Tests covering idempotency, incremental cursor, per-chat error isolation, summary idempotency, noise filter, Notion upsert, pipeline, scheduler tick, tool handlers. ✓

**Placeholder scan:** none.

**Type consistency:** `run_sync` return shape used in `sync_now_tool` and in `tick` matches definition in Task 4. `AppContext` fields match consumers in Tasks 9, 10. `TelegramPort`, `ClaudePort`, `NotionSink.upsert_summary_page` signatures match call sites. `insert_messages` row keys match `MessageDTO` and match test fixtures.

Plan complete and saved to `docs/superpowers/plans/2026-11-21-telegram-mcp-connector.md`.
