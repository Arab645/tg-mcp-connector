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


def get_digest(conn, date):
    return conn.execute("SELECT * FROM daily_digests WHERE date=?", (date,)).fetchone()

def upsert_digest(conn, date, status, covered_hash, content_md, notion_page_id,
                  model, tokens_in, tokens_out, error) -> None:
    conn.execute(
        """INSERT INTO daily_digests(date,status,covered_hash,content_md,notion_page_id,
                                     model,tokens_in,tokens_out,error,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET
             status=excluded.status, covered_hash=excluded.covered_hash,
             content_md=excluded.content_md, notion_page_id=excluded.notion_page_id,
             model=excluded.model, tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out,
             error=excluded.error, updated_at=excluded.updated_at""",
        (date, status, covered_hash, content_md, notion_page_id,
         model, tokens_in, tokens_out, error, _now()))

def done_summaries_for_date(conn, date):
    return conn.execute(
        "SELECT * FROM summaries WHERE date=? AND status='done' ORDER BY chat_id",
        (date,)).fetchall()
