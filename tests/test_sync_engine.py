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

@pytest.mark.asyncio
async def test_drain_loop_pulls_all_pages_when_more_than_batch_limit(mem_db):
    db.migrate(mem_db)
    db.upsert_chat(mem_db, 1, "A", "dm", True)
    fake = FakeTelegram(
        chats=[{"chat_id":1,"title":"A","kind":"dm"}],
        messages={1:[_m(1,i) for i in range(1,1501)]})
    res = await run_sync(mem_db, fake, batch_limit=500)
    assert res["total_inserted"] == 1500
    assert db.get_cursor(mem_db, 1) == 1500
    res2 = await run_sync(mem_db, fake, batch_limit=500)
    assert res2["total_inserted"] == 0
