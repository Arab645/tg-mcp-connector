# Telegram MCP Connector — Design Spec

**Status:** Draft for review
**Date:** 2026-11-21
**Owner:** test task for HOLYWATER TECH

## 1. Problem

Build a Telegram connector that pulls work communications (groups + DMs) into a personal "second brain." It must expose an MCP server, sync incrementally without duplicates or gaps across repeated / missed / concurrent runs, produce short structured daily summaries via an LLM (not raw dumps), and store them in a readable sink.

## 2. Goals / Non-goals

**Goals**
- MCP server (stdio) exposing tools to browse chats, send messages, trigger sync, and read summaries.
- Reliable incremental sync: per-chat cursor + idempotent writes. Zero duplicates on repeat/double runs; zero gaps on missed runs.
- Daily summaries per `(chat, date)` via Claude Haiku 5.5, filtered for noise.
- Notion database as the human-readable sink; SQLite as source of truth for state.
- Clean secret handling (dotenv + 0600 session file, nothing in git).

**Non-goals (MVP)**
- Multi-tenant. One user, one Telegram session.
- Real-time event streaming. Polling only.
- Thread-level or topic-level summaries (daily per chat is MVP).
- HA / multi-instance deployment.
- Web UI. Reading happens in Notion or via MCP tools.

## 3. Architecture

### 3.1 Components (isolated, single-purpose)

1. **`telegram/`** — Telethon wrapper. Owns session file. Thin surface: `list_chats()`, `iter_new_messages(chat_id, min_id)`, `send_message(chat_id, text)`, `resolve(chat_ref)`. No business logic.
2. **`storage/db.py`** — SQLite via `sqlite3` or SQLAlchemy Core. Schemas below. Transactional writes.
3. **`storage/notion.py`** — Notion API client. Upserts summary pages. Isolated so failure here does not block the sync pipeline.
4. **`sync/engine.py`** — pulls new messages per watchlisted chat, upserts raw, advances cursor atomically in one transaction.
5. **`sync/scheduler.py`** — APScheduler, runs sync every N minutes. Can be disabled; manual runs via MCP tool.
6. **`summarize/pipeline.py`** — picks `(chat, date)` buckets that have new/unsummarized messages, applies noise filter, calls Claude, writes summary, upserts Notion page.
7. **`summarize/prompts.py`** — prompt templates (system + user), schema for structured output.
8. **`server.py`** — MCP entrypoint (stdio). Registers tools, wires components.
9. **`config.py`** — `pydantic-settings`, reads `.env`.

### 3.2 Data flow

```
Telegram (MTProto)
    │
    ▼
telegram.iter_new_messages(chat_id, min_id=cursor)
    │
    ▼
sync.engine: one txn per chat
    ├─ INSERT OR IGNORE INTO messages (dedup on (chat_id, message_id))
    └─ UPDATE sync_cursors SET last_message_id = max_id
    │
    ▼
summarize.pipeline: find (chat, date) where
    messages.max(id) > summaries.last_covered_id OR summaries row missing
    │
    ├─ fetch day's messages from SQLite
    ├─ noise filter (heuristic + prompt instruction)
    ├─ Claude Haiku 5.5 → structured summary (JSON)
    ├─ UPSERT summaries (chat_id, date) → status=done, last_covered_id
    └─ UPSERT Notion page keyed by (chat_id, date)
```

### 3.3 MCP tool surface

| Tool | Params | Notes |
|---|---|---|
| `list_chats` | `kind?: "group"\|"dm"\|"all"`, `limit?` | Returns id, title, kind, unread count, last activity. |
| `fetch_messages` | `chat_id`, `limit=50`, `before_id?` | Raw messages from SQLite (not Telegram) — fast, read-only. |
| `send_message` | `chat_id`, `text` | Via Telethon. Rate-limited. |
| `sync_now` | `chat_ids?` | Triggers sync engine synchronously. Returns per-chat counts. |
| `get_summary` | `chat_id`, `date` | Reads from SQLite (Notion is cache for humans). |
| `list_summaries` | `date_from`, `date_to`, `chat_ids?` | Range query. |
| `set_watchlist` | `chat_ids: list[int]`, `mode: "replace"\|"add"\|"remove"` | Controls which chats sync. |
| `get_status` | — | Last run time, per-chat cursor, pending summaries, last error. |

### 3.4 Database schema (SQLite)

```sql
CREATE TABLE chats (
  chat_id      INTEGER PRIMARY KEY,
  title        TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('group','dm','channel')),
  watched      INTEGER NOT NULL DEFAULT 0,
  added_at     TEXT NOT NULL
);

CREATE TABLE messages (
  chat_id      INTEGER NOT NULL,
  message_id   INTEGER NOT NULL,
  sender_id    INTEGER,
  sender_name  TEXT,
  date_utc     TEXT NOT NULL,
  text         TEXT,
  media_kind   TEXT,
  reply_to     INTEGER,
  raw_json     TEXT NOT NULL,
  PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX idx_messages_chat_date ON messages(chat_id, date_utc);

CREATE TABLE sync_cursors (
  chat_id            INTEGER PRIMARY KEY,
  last_message_id    INTEGER NOT NULL,
  updated_at         TEXT NOT NULL
);

CREATE TABLE summaries (
  chat_id            INTEGER NOT NULL,
  date               TEXT NOT NULL,
  status             TEXT NOT NULL,  -- 'pending','done','error'
  last_covered_id    INTEGER,
  content_json       TEXT,
  notion_page_id     TEXT,
  model              TEXT,
  tokens_in          INTEGER,
  tokens_out         INTEGER,
  error              TEXT,
  updated_at         TEXT NOT NULL,
  PRIMARY KEY (chat_id, date)
);

CREATE TABLE runs (
  run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  trigger      TEXT NOT NULL,  -- 'manual','scheduled','startup'
  stats_json   TEXT,
  error        TEXT
);
```

### 3.5 Idempotency & concurrency

- **Fetch idempotency:** `INSERT OR IGNORE` on `messages` PK. Repeat fetches are no-ops.
- **Cursor advancement:** same transaction as the insert batch. If the process dies mid-batch, cursor only advances for what was committed.
- **Summary idempotency:** `summaries (chat_id, date)` PK + `last_covered_id`. A summary is regenerated only when `max(messages.id for that day) > last_covered_id`. Notion page is upserted by `(chat_id, date)` property (unique constraint in the Notion DB schema).
- **Concurrency:** single-writer via `BEGIN IMMEDIATE` on SQLite + a file-based lock (`.lock` in state dir) around a run. A second `sync_now` while one is running returns `busy` instead of racing.
- **Crash safety:** WAL mode on SQLite, fsync on commit. Partial runs resume from cursor on next start.

### 3.6 Noise filter

Two layers:

1. **Heuristic (cheap, pre-LLM):** drop stickers w/o caption, service messages (joins/leaves/pins), pure-emoji single-word replies, forwarded media without text — but keep metadata about counts so summary can mention "+N reaction-only replies."
2. **Prompt-level:** system prompt instructs Claude to ignore chit-chat, deduplicate repeated points, and extract only: topics, decisions, action items (with owner if named), open questions, and notable links. Output is JSON with those fields; Markdown rendering is a separate step.

### 3.7 Summary structure (per `(chat, date)`)

```json
{
  "topics": [{"title": "...", "summary": "..."}],
  "decisions": ["..."],
  "action_items": [{"text": "...", "owner": "..."}],
  "open_questions": ["..."],
  "links": [{"url": "...", "context": "..."}],
  "participants": ["..."],
  "message_count": 123,
  "noise_filtered_count": 45
}
```

Notion page = this rendered as nicely formatted blocks + original metadata (chat title, date, message count, source link to first/last message).

### 3.8 Secrets & config

- `.env` (gitignored) with: `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_PATH`, `ANTHROPIC_API_KEY`, `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `WATCHLIST_DEFAULT`, `SYNC_INTERVAL_MIN=15`, `SUMMARY_MODEL=claude-haiku-5-5`.
- `.env.example` committed with placeholders.
- Session file at `~/.config/tg-mcp/session.session`, mode 0600, created on first login.
- First-time login via a one-off CLI: `python -m tg_mcp.login` (interactive phone code).
- No secret ever logged. Log formatter strips known keys.

## 4. Operational behavior

- **First run:** empty cursors → each watched chat is backfilled from `offset_id=0` up to "now", bounded by configurable `MAX_BACKFILL_DAYS=7` to keep first run fast and avoid rate limits. Older history remains unseen but can be pulled with `sync_now(..., backfill_days=N)`.
- **Scheduled runs:** every `SYNC_INTERVAL_MIN`. On each: sync → then summarize `(chat, today)` and `(chat, yesterday)` if pending.
- **Manual `sync_now`:** same path, plus returns a report.
- **Errors:** per-chat errors do not fail the whole run; they are recorded in `runs.stats_json` and surfaced via `get_status`.

## 5. Testing strategy

- **Unit:** noise filter, cursor logic, summary idempotency decider, Notion payload builder.
- **Integration (SQLite, real):** sync engine with a fake Telegram client that serves canned messages → assert two runs produce identical DB state; interrupted run recovers.
- **Integration (Anthropic, mocked):** pipeline with recorded LLM responses.
- **Manual smoke:** real Telegram on a test account + real Notion DB; demo script in README.

## 6. Repo layout

```
tg-mcp-connector/
├── src/tg_mcp/
│   ├── __init__.py
│   ├── server.py
│   ├── config.py
│   ├── login.py
│   ├── tools/
│   │   ├── __init__.py
│   │   └── handlers.py
│   ├── telegram/
│   │   ├── __init__.py
│   │   └── client.py
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py
│   │   ├── migrations.py
│   │   └── notion.py
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── scheduler.py
│   └── summarize/
│       ├── __init__.py
│       ├── pipeline.py
│       ├── prompts.py
│       └── claude.py
├── tests/
│   ├── test_sync_engine.py
│   ├── test_noise_filter.py
│   ├── test_summary_idempotency.py
│   └── fixtures/
├── docs/
│   └── superpowers/specs/2026-11-21-telegram-mcp-connector-design.md
├── scripts/
│   └── demo.sh
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── LICENSE
```

## 7. Trade-offs

- **MTProto user session vs Bot API:** chose user session because the task calls out DMs and chats the user already participates in. Accepts the risk of ToS-level account scrutiny.
- **Notion sink vs plain Markdown:** chose Notion because the task names it first and it matches the "second brain" framing. Accepts added failure mode; mitigated by SQLite being source of truth and Notion being an upsert sink.
- **Polling vs real-time Telethon event handler:** polling for MVP — deterministic, simpler recovery semantics, no long-lived process assumption. Real-time is a later layer that would feed the same engine.
- **Daily per-chat summaries vs threads:** task explicitly says "денні самарі." Threaded summaries would require conversation segmentation that adds complexity without a clear MVP payoff.
- **SQLite vs Postgres:** single-user local tool. SQLite with WAL is sufficient and removes a dependency.

## 8. Limits (honest, in README)

- Account-ban risk inherent to MTProto user sessions.
- First backfill on very active chats may hit Telethon flood-wait; mitigated by chunking and bounded backfill window.
- Summary quality depends on language mix; prompt is tuned for RU/UA/EN.
- Single-instance — no HA. A second instance would double-send or race on Notion unless pointed at a different DB.
- No deletion handling: deleted/edited messages are not reconciled in MVP. Could be added via Telethon updates later.

## 9. Open questions (none blocking)

- Final Notion DB schema (properties) — will be fixed when the pipeline is implemented; design below is draft.
- Exact noise-filter thresholds — tune during manual smoke.
