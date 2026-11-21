#!/usr/bin/env bash
# Demo: two back-to-back sync passes against the real Telegram session.
# Proves the sync engine is idempotent: the second pass must insert 0 rows.
# Requires: .env filled in, `tg-mcp-login` already run once.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"  # pydantic-settings reads .env from the working directory

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

"$PY" - <<'PY'
import asyncio, json

from tg_mcp.config import load_settings
from tg_mcp.storage import db
from tg_mcp.telegram.client import TelethonTelegram
from tg_mcp.tools.handlers import AppContext, sync_now_tool


async def main():
    s = load_settings()
    conn = db.connect(s.tg_mcp_db_path)
    db.migrate(conn)

    async with TelethonTelegram(s.tg_api_id, s.tg_api_hash, s.tg_session_path) as tg:
        # Watch the chats named in WATCHLIST_DEFAULT, using their real titles.
        if s.watchlist_default:
            known = {c["chat_id"]: c for c in await tg.list_chats()}
            for cid in s.watchlist_default:
                info = known.get(cid, {"title": f"chat:{cid}", "kind": "group"})
                db.upsert_chat(conn, cid, info["title"], info["kind"], True)
        else:
            raise SystemExit("WATCHLIST_DEFAULT is empty; nothing to sync.")

        # sync_now is the same code path the MCP tool and the scheduler use.
        app = AppContext(conn=conn, tg=tg, claude=None, notion=None, settings=s)
        first = await sync_now_tool(app)
        second = await sync_now_tool(app)

    report = {
        "first_run_inserted": first["total_inserted"],
        "second_run_inserted": second["total_inserted"],
        "second_run_per_chat": {str(k): v["inserted"] for k, v in second["per_chat"].items()},
    }
    print(json.dumps(report, indent=2))

    # run_sync records per-chat failures instead of raising, so a dead
    # connection would otherwise show up as 0 inserts and a false pass.
    failed = False
    for label, run in (("first", first), ("second", second)):
        for cid, res in run["per_chat"].items():
            if res["error"] is not None:
                failed = True
                print(f"{label} pass, chat {cid}: {res['error']}")
    if failed:
        raise SystemExit(1)

    if first["total_inserted"] == 0:
        print("note: watchlist already fully synced; idempotency check still valid")

    # A chat that receives a message between the two passes will make this
    # non-zero; that is new data, not a duplicate. Run on a quiet watchlist.
    assert second["total_inserted"] == 0, \
        "second pass inserted rows: duplicates or new messages arrived between passes"


asyncio.run(main())
PY
