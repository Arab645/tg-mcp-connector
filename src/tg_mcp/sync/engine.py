# src/tg_mcp/sync/engine.py
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from ..storage import db
from ..telegram.client import TelegramPort

async def run_sync(conn, tg: TelegramPort, chat_ids=None, batch_limit=500,
                   max_backfill_days: int | None = None) -> dict:
    if chat_ids is None:
        chat_ids = [r["chat_id"] for r in db.list_chats(conn, watched_only=True)]
    per_chat: dict[int, dict] = {}
    total = 0
    for cid in chat_ids:
        cursor = db.get_cursor(conn, cid)
        start_cursor = cursor
        inserted = 0
        err = None
        try:
            offset_date = None
            if cursor is None and max_backfill_days is not None:
                offset_date = datetime.now(timezone.utc) - timedelta(days=max_backfill_days)
            while True:
                batch: list[dict] = []
                kwargs = {"offset_date": offset_date} if offset_date is not None else {}
                async for m in tg.iter_new_messages(cid, min_id=cursor, limit=batch_limit, **kwargs):
                    batch.append(m)
                offset_date = None  # only the first page is date-bounded
                inserted += db.insert_messages(conn, batch)
                if batch:
                    cursor = max(m["message_id"] for m in batch)
                    db.set_cursor(conn, cid, cursor)
                if len(batch) < batch_limit:
                    break
            per_chat[cid] = {"inserted": inserted, "new_cursor": cursor, "error": None}
            total += inserted
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            per_chat[cid] = {"inserted": 0, "new_cursor": start_cursor, "error": err}
    run_id = db.record_run(conn, "manual")
    db.finish_run(conn, run_id, json.dumps(per_chat), None)
    return {"per_chat": per_chat, "total_inserted": total}
