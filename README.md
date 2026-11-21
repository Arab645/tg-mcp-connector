# tg-mcp-connector

A Telegram connector that pulls work chats (groups and DMs) into a local store, produces a short daily LLM summary per chat, and exposes everything as an MCP server so Claude Desktop or Claude Code can browse, sync, send, and read summaries.

Built as a test task for HOLYWATER TECH. The full design (goals, schema, idempotency argument, trade-offs) is in [`docs/superpowers/specs/2026-11-21-telegram-mcp-connector-design.md`](docs/superpowers/specs/2026-11-21-telegram-mcp-connector-design.md). This README describes what is actually built; where the code differs from the original spec, the difference is called out under [Limits](#limits).

The core idea: **SQLite is the source of truth; Notion is a human-readable sink.** Sync is incremental (a per-chat cursor), and every write is idempotent, so re-running a sync, or having a run die halfway, never creates duplicates or gaps.

## Setup

Requires Python 3.11+ (developed on 3.12) and a Telegram API application (api_id / api_hash from https://my.telegram.org).

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill it in (see below)
tg-mcp-login           # interactive, once: phone number + login code
```

`.env` keys (see `.env.example`):

| Key | Purpose |
|---|---|
| `TG_API_ID`, `TG_API_HASH` | Telegram application credentials |
| `TG_SESSION_PATH` | Telethon session file (default `~/.config/tg-mcp/session`); created with mode 0600 |
| `ANTHROPIC_API_KEY`, `SUMMARY_MODEL` | Summarizer; defaults to `claude-haiku-5-5` |
| `NOTION_TOKEN`, `NOTION_DATABASE_ID` | Notion integration and target database |
| `TG_MCP_DB_PATH` | SQLite file (default `./data/tg_mcp.sqlite3`) |
| `SYNC_INTERVAL_MIN` | Scheduled sync interval, default 15 |
| `WATCHLIST_DEFAULT` | Comma-separated chat IDs to watch at startup |

`.env` is gitignored. Secrets are only read from the environment or `.env`; nothing is logged by this code.

The Notion database needs a `Key` (rich text) property. Summary pages are looked up by `Key = "<chat_id>:<date>"`, and the other properties (`Name`, `Date`, `ChatId`, `MessageCount`) are written by the connector.

## Running

Stdio MCP server, started by the client:

```bash
tg-mcp                 # or: .venv/bin/tg-mcp
```

Claude Desktop / Claude Code config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "tg-mcp": {
      "command": "/absolute/path/to/repo/.venv/bin/tg-mcp",
      "env": {
        "TG_API_ID": "...",
        "TG_API_HASH": "...",
        "TG_SESSION_PATH": "/Users/you/.config/tg-mcp/session",
        "ANTHROPIC_API_KEY": "...",
        "NOTION_TOKEN": "...",
        "NOTION_DATABASE_ID": "...",
        "TG_MCP_DB_PATH": "/absolute/path/to/repo/data/tg_mcp.sqlite3",
        "WATCHLIST_DEFAULT": "-1001234567890,123456789"
      }
    }
  }
}
```

Use absolute paths. Settings are read from `.env` in the *current working directory*, and a desktop client does not start the server from the repo, so the relative defaults (`./data/...`, `.env`) would point at the wrong place. Environment variables in the `env` block are read by the same settings loader.

On startup the server opens the database, runs migrations, registers the `WATCHLIST_DEFAULT` chats, connects to Telegram with the saved session, and starts the scheduler (which runs one sync immediately, then every `SYNC_INTERVAL_MIN` minutes).

## Daily digest

After per-chat summaries, each scheduler tick builds a cross-chat digest for `today` and `yesterday` (UTC). `summarize/digest.py` collects the `done` per-chat summaries for the date, hashes their `(chat_id, last_covered_id)` pairs, and calls Claude once to write a short Markdown brief (what happened, decisions, action items, open questions, links). The result is stored in the `daily_digests` table. If the hash matches the stored digest, nothing is regenerated, so idle ticks cost nothing; a new or advanced per-chat summary changes the hash and triggers a rewrite.

When Notion is configured, the digest is upserted into the same database as the per-chat pages, keyed by `Key = "digest:<date>"` with `ChatId = 0` as the digest sentinel. Read it with `get_digest` and `list_digests`.

## Data flow

```
Telegram (MTProto)
    │
    ▼
telegram.iter_new_messages(chat_id, min_id=cursor)
    │
    ▼
sync.engine: one insert txn per chat, then cursor update
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

The scheduler (`sync/scheduler.py`) runs `tick()`: a sync of all watched chats, then summaries for `today` and `yesterday` (UTC). Most MCP tools read from SQLite. The exceptions that call Telegram are `sync_now` (a sync), `send_message`, and `set_watchlist` (only for chats it has not seen before).

Module map (`src/tg_mcp/`):

| Module | Role |
|---|---|
| `server.py` | MCP stdio entrypoint; tool schemas and dispatcher |
| `tools/handlers.py` | The 8 tool implementations over an `AppContext` |
| `telegram/client.py` | `TelegramPort` protocol, `TelethonTelegram` (real), `FakeTelegram` (tests) |
| `sync/engine.py` | `run_sync`: per-chat incremental fetch, insert, cursor advance |
| `sync/scheduler.py` | APScheduler job: sync then summarize |
| `summarize/pipeline.py` | `summarize_day`, `run_pending_summaries`: decides what needs a (re)summary |
| `summarize/filter.py`, `prompts.py`, `claude.py` | Noise filter, prompts, Anthropic client |
| `storage/db.py`, `migrations.py` | SQLite schema and access functions |
| `storage/notion.py` | Notion upsert of summary pages |
| `config.py` | `pydantic-settings` loader for `.env` |
| `login.py` | One-off interactive Telegram login |

## MCP tools reference

| Tool | Params | What it does |
|---|---|---|
| `list_chats` | `kind?: "all"\|"dm"\|"group"\|"channel"`, `limit?` (100) | Chats known to the local store (id, title, kind, watched flag). Reads SQLite. |
| `fetch_messages` | `chat_id`, `limit?` (50), `before_id?` | Raw messages from SQLite, newest first. Read-only, no Telegram call. |
| `send_message` | `chat_id`, `text` | Sends via Telethon. Returns the new message id. |
| `sync_now` | `chat_ids?` | Runs the sync engine now for the given chats (default: watched). Returns per-chat inserted counts and cursors. |
| `get_summary` | `chat_id`, `date` (YYYY-MM-DD) | One day's summary row (JSON content, status, Notion page id, token counts), or null. |
| `list_summaries` | `date_from`, `date_to`, `chat_ids?` | Summary rows in a date range, newest first. |
| `get_digest` | `date` (YYYY-MM-DD) | The cross-chat daily digest row (Markdown content, status, covered hash, Notion page id, token counts), or null. |
| `list_digests` | `date_from`, `date_to` | Digest rows in a date range, newest first. |
| `set_watchlist` | `chat_ids`, `mode?: "replace"\|"add"\|"remove"` (replace) | Sets which chats sync. Unknown chats are looked up on Telegram and stored unwatched. Returns the watched list. |
| `get_status` | none | Last run (with per-chat stats JSON and error), all sync cursors, and summaries not in `done` status. |

Tool results are JSON in a single text content block.

## Design decisions

The spec has the full reasoning; the short version:

- **Cursor plus `INSERT OR IGNORE`.** A per-chat `last_message_id` says where to resume; the `(chat_id, message_id)` primary key makes any re-fetch harmless. Cursor advances only for batches that were fetched and inserted, so a crash mid-run causes re-fetching, never skipping.
- **Summaries keyed by `(chat, date)` with `last_covered_id`.** A day is re-summarized only when it has messages newer than the last summarized id, so repeated runs cost nothing once a day is current.
- **Polling, not Telethon event handlers.** Deterministic, easy to reason about after a crash, and no long-lived assumptions. Real-time would feed the same engine later.
- **Noise filter before the LLM.** Stickers, service messages, acknowledgements ("ok", "спс", "+1"), and emoji-only messages are dropped in code, which saves tokens and makes the drop count auditable. The prompt also tells the model to ignore chit-chat.
- **Structured JSON output.** The model returns topics, decisions, action items with owners, open questions, and links. Notion rendering is a separate step, so the same data can serve MCP clients and the sink.
- **Notion failure does not block sync.** Sync and summarization are separate steps, and a Notion write happens after the LLM call. A failed Notion write marks that day's summary `error`; messages and cursors are unaffected, and the next run retries the day.
- **Insert and cursor are not one transaction.** `insert_messages` commits its own `BEGIN IMMEDIATE` transaction, and `set_cursor` is a separate autocommit statement. Safety holds because a crash between them re-fetches the batch on the next run, and the `(chat_id, message_id)` primary key absorbs the duplicates.
- **Per-chat error isolation.** One chat failing (for example, a flood wait) is recorded in `runs.stats_json` and does not stop the others.

## Trade-offs

- **User session (MTProto) instead of a Bot API token.** Required to read DMs and groups the user is already in. The cost is that this is a user account automation, with the ToS and ban risk that comes with it.
- **Notion plus SQLite instead of Markdown files.** Notion is what the brief names and fits the "second brain" use. It adds a failure mode and an API dependency; SQLite remains authoritative, so Notion can be rebuilt.
- **Polling instead of push.** Simpler recovery, at the price of up to `SYNC_INTERVAL_MIN` of latency.
- **Daily per-chat summaries instead of per-thread.** Threads would need conversation segmentation, which is more complexity than the MVP needs.
- **SQLite instead of Postgres.** Single-user, local, one writer. WAL mode is enough and removes a service.

## Limits

These are the known limits of the current build. Some come from the spec's MVP scope; others are gaps between the spec and the code, listed so a reviewer does not have to find them.

- **Account risk.** A user-session automation can be restricted or banned by Telegram.
- **No flood-wait handling.** Telethon flood waits surface as per-chat errors in `runs.stats_json`. There is no backoff or retry, and `send_message` has no rate limiter, despite the spec saying it would.
- **First-run backfill is bounded by `MAX_BACKFILL_DAYS`** (default 7). The engine drains each chat page-by-page until the whole visible window is caught up in a single run, so a first-time sync of a busy chat completes in one pass. Expanding the window needs raising `MAX_BACKFILL_DAYS` and re-running. The spec's ad-hoc `sync_now(backfill_days=N)` parameter is not exposed via the MCP tool.
- **No run lock.** The spec describes a `BEGIN IMMEDIATE` plus `.lock` file so a second `sync_now` returns `busy`. The code has `BEGIN IMMEDIATE` on inserts only. There is no lock file and no `busy` response. Concurrent runs (a manual `sync_now` during a scheduled tick) are safe for message rows because of the primary key, but the cursor can be written by whichever run finishes last. Run one server per database.
- **`list_chats` shows only known chats.** Chats appear in the store when they are in `WATCHLIST_DEFAULT` or added through `set_watchlist`. It does not list all Telegram dialogs, and it has no unread count or last-activity fields.
- **Summaries run from the scheduler only.** `sync_now` syncs messages; it does not generate summaries. Summaries appear on the next scheduler tick.
- **`get_status` lists errors, not "pending".** Summaries are written as `done` or `error`; `pending` is never written.
- **Summary errors overwrite prior content.** When a regeneration fails, the row is set to `error` with `content_json` cleared. `get_summary` then returns empty content until the next successful run. The Notion page from the previous success is kept. Fixing this needs a read-modify-write that keeps the last good payload.
- **Notion lookup is not enforced.** Pages are found by querying the `Key` property and then created or patched. Notion does not enforce uniqueness on that property, so two concurrent writers could create duplicate pages for one `(chat, date)`. Single-instance operation avoids this. The spec's "unique constraint" was not added to the database schema.
- **No source links or reaction counts in summaries.** The spec's links to the first and last message and the "+N reaction-only replies" note are not implemented; the summary records `noise_filtered_count` instead.
- **Edits and deletes are not reconciled.** A message edited or deleted in Telegram keeps its stored text.
- **Language.** The summary prompt asks for the dominant language (RU, UA, EN). Quality on mixed-language chats has not been measured beyond the manual smoke run.
- **Logging.** The code does not log secrets, but there is no log formatter that strips known keys, as the spec describes.
- **Single instance, no HA.** A second process pointed at the same database would race on cursors, and at the same Notion database would race on page creation.

## Demo

With `.env` filled in and `tg-mcp-login` done:

```bash
./scripts/demo.sh
```

The script connects to the real Telegram session, watches the chats in `WATCHLIST_DEFAULT` (with their real titles), and calls the same `sync_now` code the MCP tool uses **twice in a row**. It prints the inserted counts for each pass and asserts that the **second pass inserts 0 rows**, which shows the sync is idempotent: the cursor and the primary key prevent duplicates on a repeat run. A per-chat error in either pass (for example, an auth or network failure) fails the run with exit 1, so a dead connection cannot pass as "0 inserted".

If a message arrives in a watched chat between the two passes, the second count is non-zero. That is new data, not a duplicate, and the assertion will fail. Run the demo on a quiet watchlist, or read the per-chat counts it prints.

The automated suite (no network, no credentials) runs with:

```bash
.venv/bin/pytest -q
```

It covers the sync engine (idempotency, cursor advancement, per-chat error isolation), the noise filter, the summary pipeline, the Notion payload and upsert against a mocked HTTP transport, the scheduler tick, the MCP tool handlers, and the config loader.
