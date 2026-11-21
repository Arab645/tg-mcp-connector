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
