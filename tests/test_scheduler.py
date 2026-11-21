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
