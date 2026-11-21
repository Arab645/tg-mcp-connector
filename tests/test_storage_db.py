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
