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
