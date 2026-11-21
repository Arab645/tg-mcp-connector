# src/tg_mcp/tools/handlers.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from ..storage import db
from ..sync.engine import run_sync

@dataclass
class AppContext:
    conn: Any
    tg: Any
    claude: Any
    notion: Any
    settings: Any

def _row(r): return {k: r[k] for k in r.keys()}

async def list_chats_tool(ctx: AppContext, kind: str = "all", limit: int = 100) -> list[dict]:
    rows = db.list_chats(ctx.conn)
    if kind != "all":
        rows = [r for r in rows if r["kind"] == kind]
    return [_row(r) for r in rows[:limit]]

async def fetch_messages_tool(ctx: AppContext, chat_id: int, limit: int = 50,
                              before_id: int | None = None) -> list[dict]:
    q = "SELECT * FROM messages WHERE chat_id=?"
    args: list = [chat_id]
    if before_id is not None:
        q += " AND message_id < ?"; args.append(before_id)
    q += " ORDER BY message_id DESC LIMIT ?"; args.append(limit)
    return [_row(r) for r in ctx.conn.execute(q, args).fetchall()]

async def send_message_tool(ctx: AppContext, chat_id: int, text: str) -> dict:
    mid = await ctx.tg.send_message(chat_id, text)
    return {"chat_id": chat_id, "message_id": mid}

async def sync_now_tool(ctx: AppContext, chat_ids: list[int] | None = None) -> dict:
    return await run_sync(
        ctx.conn, ctx.tg, chat_ids=chat_ids,
        max_backfill_days=getattr(ctx.settings, "max_backfill_days", None))

async def get_summary_tool(ctx: AppContext, chat_id: int, date: str) -> dict | None:
    row = db.get_summary(ctx.conn, chat_id, date)
    return _row(row) if row else None

async def list_summaries_tool(ctx: AppContext, date_from: str, date_to: str,
                              chat_ids: list[int] | None = None) -> list[dict]:
    q = "SELECT * FROM summaries WHERE date BETWEEN ? AND ?"
    args: list = [date_from, date_to]
    if chat_ids:
        q += " AND chat_id IN (" + ",".join("?" * len(chat_ids)) + ")"
        args.extend(chat_ids)
    q += " ORDER BY date DESC, chat_id"
    return [_row(r) for r in ctx.conn.execute(q, args).fetchall()]

async def set_watchlist_tool(ctx: AppContext, chat_ids: list[int], mode: str = "replace") -> dict:
    if mode not in ("replace", "add", "remove"):
        raise ValueError(f"bad mode: {mode}")
    # Ensure chats exist; if not, hydrate from Telegram.
    existing = {r["chat_id"] for r in db.list_chats(ctx.conn)}
    needed = [c for c in chat_ids if c not in existing]
    if needed:
        for info in await ctx.tg.list_chats():
            if info["chat_id"] in needed:
                db.upsert_chat(ctx.conn, info["chat_id"], info["title"], info["kind"], False)
    if mode == "replace":
        for r in db.list_chats(ctx.conn):
            db.set_watched(ctx.conn, r["chat_id"], r["chat_id"] in chat_ids)
    elif mode == "add":
        for c in chat_ids: db.set_watched(ctx.conn, c, True)
    else:  # remove
        for c in chat_ids: db.set_watched(ctx.conn, c, False)
    watched = [r["chat_id"] for r in db.list_chats(ctx.conn, watched_only=True)]
    return {"watched": watched}

async def get_status_tool(ctx: AppContext) -> dict:
    last_run = ctx.conn.execute(
        "SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    cursors = {r["chat_id"]: r["last_message_id"]
               for r in ctx.conn.execute("SELECT * FROM sync_cursors").fetchall()}
    pending = ctx.conn.execute(
        "SELECT chat_id,date,status FROM summaries WHERE status!='done' "
        "ORDER BY date DESC LIMIT 20").fetchall()
    return {
        "last_run": _row(last_run) if last_run else None,
        "cursors": cursors,
        "pending_summaries": [_row(r) for r in pending],
    }


async def get_digest_tool(ctx, date: str):
    row = db.get_digest(ctx.conn, date)
    return _row(row) if row else None

async def list_digests_tool(ctx, date_from: str, date_to: str):
    q = "SELECT * FROM daily_digests WHERE date BETWEEN ? AND ? ORDER BY date DESC"
    return [_row(r) for r in ctx.conn.execute(q, (date_from, date_to)).fetchall()]
