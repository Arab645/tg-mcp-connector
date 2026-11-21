# src/tg_mcp/sync/engine.py
from __future__ import annotations
import json
from ..storage import db
from ..telegram.client import TelegramPort

async def run_sync(conn, tg: TelegramPort, chat_ids=None, batch_limit=500) -> dict:
    if chat_ids is None:
        chat_ids = [r["chat_id"] for r in db.list_chats(conn, watched_only=True)]
    per_chat: dict[int, dict] = {}
    total = 0
    for cid in chat_ids:
        cursor = db.get_cursor(conn, cid)
        batch: list[dict] = []
        err = None
        try:
            async for m in tg.iter_new_messages(cid, min_id=cursor, limit=batch_limit):
                batch.append(m)
            inserted = db.insert_messages(conn, batch)
            new_cursor = max((m["message_id"] for m in batch), default=cursor)
            if batch:
                db.set_cursor(conn, cid, new_cursor)
            per_chat[cid] = {"inserted": inserted, "new_cursor": new_cursor, "error": None}
            total += inserted
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            per_chat[cid] = {"inserted": 0, "new_cursor": cursor, "error": err}
    run_id = db.record_run(conn, "manual")
    db.finish_run(conn, run_id, json.dumps(per_chat), None)
    return {"per_chat": per_chat, "total_inserted": total}
