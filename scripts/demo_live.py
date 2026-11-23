#!/usr/bin/env python3
"""Live demo loop: tick every N seconds, print what changed.

Usage:
    .venv/bin/python scripts/demo_live.py            # 20s interval (default)
    .venv/bin/python scripts/demo_live.py 10         # 10s interval
    .venv/bin/python scripts/demo_live.py 10 --reset # wipe DB first

Press Ctrl-C to stop.
"""
from __future__ import annotations
import asyncio, os, sys, time
from datetime import datetime, timezone

from tg_mcp.config import load_settings
from tg_mcp.storage import db
from tg_mcp.storage.notion import NotionSink
from tg_mcp.telegram.client import TelethonTelegram
from tg_mcp.summarize.claude import AnthropicClaude
from tg_mcp.tools.handlers import AppContext
from tg_mcp.sync.scheduler import tick


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _hr(ch: str = "─") -> str:
    return ch * 72


async def main(interval: int, reset: bool) -> None:
    s = load_settings()

    if reset:
        db_path = s.tg_mcp_db_path
        if db_path.exists():
            db_path.unlink()
            print(f"[{_now()}] RESET: removed {db_path}")

    conn = db.connect(s.tg_mcp_db_path)
    db.migrate(conn)

    print(_hr("="))
    print(f"  tg-mcp live demo loop — tick every {interval}s")
    print(f"  watchlist: {s.watchlist_default}")
    print(f"  Ctrl-C to stop.")
    print(_hr("="))

    async with TelethonTelegram(s.tg_api_id, s.tg_api_hash, s.tg_session_path) as tg:
        # Hydrate watchlist titles (same as server startup)
        known = {c["chat_id"]: c for c in await tg.list_chats()}
        for cid in s.watchlist_default:
            if cid in known:
                db.upsert_chat(conn, cid, known[cid]["title"], known[cid]["kind"], True)
            else:
                existing = conn.execute("SELECT 1 FROM chats WHERE chat_id=?", (cid,)).fetchone()
                if existing:
                    db.set_watched(conn, cid, True)
                else:
                    db.upsert_chat(conn, cid, f"chat:{cid}", "group", True)

        notion = NotionSink(s.notion_token, s.notion_database_id)
        claude = AnthropicClaude(s.anthropic_api_key, s.summary_model)
        ctx = AppContext(conn=conn, tg=tg, claude=claude, notion=notion, settings=s)

        tick_n = 0
        try:
            while True:
                tick_n += 1
                print(f"\n[{_now()}] tick #{tick_n} starting...")
                t0 = time.monotonic()
                report = await tick(ctx)
                dt = time.monotonic() - t0

                synced = report["sync"]["total_inserted"]
                summaries = report["summaries"]
                digests = report["digests"]

                print(f"[{_now()}] tick #{tick_n} done in {dt:.1f}s")
                if synced:
                    print(f"  ⇣  SYNC: +{synced} new messages")
                    for cid, r in report["sync"]["per_chat"].items():
                        if r["inserted"] > 0:
                            title = conn.execute(
                                "SELECT title FROM chats WHERE chat_id=?", (cid,)
                            ).fetchone()
                            print(f"     • {title['title'] if title else cid}: +{r['inserted']}")
                else:
                    print(f"  ⇣  SYNC: no new messages")

                if summaries:
                    print(f"  ⇡  SUMMARIES regenerated for: {list(summaries.keys())}")
                else:
                    print(f"  ⇡  SUMMARIES: nothing to (re-)summarize")

                if digests:
                    done_dates = [d for d, st in digests.items() if st == "done"]
                    skipped_dates = [d for d, st in digests.items() if st == "skipped"]
                    if done_dates:
                        print(f"  ★  DIGEST generated for: {done_dates}")
                    if skipped_dates:
                        print(f"  ★  DIGEST skipped for:   {skipped_dates} (unchanged)")

                print(f"[{_now()}] sleeping {interval}s...")
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            print(f"\n[{_now()}] stopped by user.")
        finally:
            await notion.close()


if __name__ == "__main__":
    interval = 20
    reset = False
    args = sys.argv[1:]
    for a in args:
        if a == "--reset":
            reset = True
        elif a.isdigit():
            interval = int(a)
        else:
            print(__doc__)
            sys.exit(2)
    if interval < 5:
        print("Minimum interval is 5s (Telegram/Anthropic rate limits).")
        sys.exit(2)
    asyncio.run(main(interval, reset))
