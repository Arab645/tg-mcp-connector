from __future__ import annotations
import hashlib, json
from ..storage import db
from .digest_prompts import DIGEST_SYSTEM_PROMPT, build_digest_user_prompt

def _covered_hash(rows) -> str:
    sig = sorted((r["chat_id"], r["last_covered_id"] or 0) for r in rows)
    return hashlib.sha1(repr(sig).encode("utf-8")).hexdigest()

async def digest_day(conn, date: str, claude, notion, model_id: str) -> dict:
    """Generate (or upsert) a cross-chat daily digest for `date`.
    Returns a dict with keys: status, covered_hash, content_md, notion_page_id, skipped (bool).
    """
    rows = db.done_summaries_for_date(conn, date)
    if not rows:
        return {"status": "skipped", "reason": "no done per-chat summaries", "skipped": True}

    chash = _covered_hash(rows)
    existing = db.get_digest(conn, date)
    if existing and existing["status"] == "done" and existing["covered_hash"] == chash:
        return {"status": "done", "covered_hash": chash, "skipped": True}

    per_chat = []
    total_msgs = 0
    for r in rows:
        summary = json.loads(r["content_json"] or "{}")
        chat = conn.execute("SELECT title FROM chats WHERE chat_id=?", (r["chat_id"],)).fetchone()
        title = chat["title"] if chat else str(r["chat_id"])
        mc = summary.get("message_count") or 0
        total_msgs += mc
        per_chat.append({
            "chat_title": title, "chat_id": r["chat_id"],
            "summary": summary, "message_count": mc,
        })

    try:
        prompt = build_digest_user_prompt(date, per_chat)
        resp = await claude.generate_digest(date, prompt)   # returns (md_text, tin, tout)
        content_md, tin, tout = resp
        page_id = None
        if notion is not None:
            page_id = await notion.upsert_digest_page(date, content_md, len(per_chat), total_msgs)
        db.upsert_digest(conn, date, "done", chash, content_md, page_id, model_id, tin, tout, None)
        return {"status": "done", "covered_hash": chash, "content_md": content_md,
                "notion_page_id": page_id, "skipped": False}
    except Exception as e:
        db.upsert_digest(conn, date, "error", chash, None, None, model_id, None, None,
                         f"{type(e).__name__}: {e}")
        raise

async def run_pending_digests(conn, claude, notion, model_id, dates: list[str]) -> dict:
    """For each date in `dates`, call digest_day; collect status per date."""
    out: dict = {}
    for d in dates:
        try:
            r = await digest_day(conn, d, claude, notion, model_id)
            out[d] = r.get("status", "unknown")
        except Exception as e:
            out[d] = f"error:{e}"
    return out
