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
