import json, pytest
from tg_mcp.storage import db
from tg_mcp.summarize.claude import FakeClaude
from tg_mcp.summarize.digest import digest_day, run_pending_digests

def _seed(conn):
    db.migrate(conn)
    db.upsert_chat(conn, 1, "A", "dm", True)
    db.upsert_chat(conn, 2, "B", "group", True)
    db.upsert_summary(conn, 1, "2026-11-21", "done", 10,
        json.dumps({"topics":[{"title":"t1","summary":"s1"}],"message_count":5}),
        "pg1", "m", 100, 50, None)
    db.upsert_summary(conn, 2, "2026-11-21", "done", 20,
        json.dumps({"topics":[{"title":"t2","summary":"s2"}],"message_count":8}),
        "pg2", "m", 110, 60, None)

@pytest.mark.asyncio
async def test_digest_writes_row(mem_db):
    _seed(mem_db)
    fake = FakeClaude({}, digest_response="## What happened today\nn...")
    out = await digest_day(mem_db, "2026-11-21", fake, None, "claude-haiku-5-5")
    assert out["status"] == "done"
    row = db.get_digest(mem_db, "2026-11-21")
    assert row["status"] == "done"
    assert row["content_md"].startswith("## What happened today")
    assert row["covered_hash"]

@pytest.mark.asyncio
async def test_digest_is_idempotent_when_covered_hash_unchanged(mem_db):
    _seed(mem_db)
    fake = FakeClaude({}, digest_response="## What happened today\nx")
    await digest_day(mem_db, "2026-11-21", fake, None, "claude-haiku-5-5")
    # second call: nothing changed in per-chat summaries
    out = await digest_day(mem_db, "2026-11-21", fake, None, "claude-haiku-5-5")
    assert out["skipped"] is True

@pytest.mark.asyncio
async def test_digest_regenerates_when_new_per_chat_summary(mem_db):
    _seed(mem_db)
    fake = FakeClaude({}, digest_response="## What happened today\nv1")
    await digest_day(mem_db, "2026-11-21", fake, None, "claude-haiku-5-5")
    # Advance chat 1's last_covered_id (simulates a new per-chat summary pass)
    db.upsert_summary(mem_db, 1, "2026-11-21", "done", 99,
        json.dumps({"topics":[],"message_count":5}), "pg1", "m", 100, 50, None)
    fake2 = FakeClaude({}, digest_response="## What happened today\nv2")
    out = await digest_day(mem_db, "2026-11-21", fake2, None, "claude-haiku-5-5")
    assert out["skipped"] is False
    assert db.get_digest(mem_db, "2026-11-21")["content_md"].endswith("v2")
