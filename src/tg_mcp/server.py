# src/tg_mcp/server.py
from __future__ import annotations
import asyncio, json, sys
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams, CallToolResult, ListToolsResult,
    PaginatedRequestParams, TextContent, Tool,
)
from .config import load_settings
from .storage import db
from .storage.notion import NotionSink
from .telegram.client import TelethonTelegram
from .summarize.claude import AnthropicClaude
from .sync.scheduler import start_scheduler
from .tools.handlers import (
    AppContext, list_chats_tool, fetch_messages_tool, send_message_tool,
    sync_now_tool, get_summary_tool, list_summaries_tool,
    get_digest_tool, list_digests_tool,
    set_watchlist_tool, get_status_tool,
)

TOOL_DEFS = [
    Tool(name="list_chats", description="List known chats",
         inputSchema={"type":"object","properties":{
             "kind":{"type":"string","enum":["all","dm","group","channel"],"default":"all"},
             "limit":{"type":"integer","default":100}}}),
    Tool(name="fetch_messages", description="Read raw messages from local store",
         inputSchema={"type":"object","required":["chat_id"],"properties":{
             "chat_id":{"type":"integer"}, "limit":{"type":"integer","default":50},
             "before_id":{"type":["integer","null"]}}}),
    Tool(name="send_message", description="Send a Telegram message",
         inputSchema={"type":"object","required":["chat_id","text"],"properties":{
             "chat_id":{"type":"integer"}, "text":{"type":"string"}}}),
    Tool(name="sync_now", description="Run an incremental sync now",
         inputSchema={"type":"object","properties":{
             "chat_ids":{"type":["array","null"],"items":{"type":"integer"}}}}),
    Tool(name="get_summary", description="Get one day's summary for a chat",
         inputSchema={"type":"object","required":["chat_id","date"],"properties":{
             "chat_id":{"type":"integer"}, "date":{"type":"string"}}}),
    Tool(name="list_summaries", description="List summaries in a date range",
         inputSchema={"type":"object","required":["date_from","date_to"],"properties":{
             "date_from":{"type":"string"}, "date_to":{"type":"string"},
             "chat_ids":{"type":["array","null"],"items":{"type":"integer"}}}}),
    Tool(name="get_digest", description="Get the cross-chat daily digest for a date",
         inputSchema={"type":"object","required":["date"],"properties":{
             "date":{"type":"string"}}}),
    Tool(name="list_digests", description="List daily digests in a date range",
         inputSchema={"type":"object","required":["date_from","date_to"],"properties":{
             "date_from":{"type":"string"}, "date_to":{"type":"string"}}}),
    Tool(name="set_watchlist", description="Replace/add/remove watched chats",
         inputSchema={"type":"object","required":["chat_ids"],"properties":{
             "chat_ids":{"type":"array","items":{"type":"integer"}},
             "mode":{"type":"string","enum":["replace","add","remove"],"default":"replace"}}}),
    Tool(name="get_status", description="Last run, cursors, pending summaries",
         inputSchema={"type":"object","properties":{}}),
]

HANDLERS = {
    "list_chats": list_chats_tool, "fetch_messages": fetch_messages_tool,
    "send_message": send_message_tool, "sync_now": sync_now_tool,
    "get_summary": get_summary_tool, "list_summaries": list_summaries_tool,
    "get_digest": get_digest_tool, "list_digests": list_digests_tool,
    "set_watchlist": set_watchlist_tool, "get_status": get_status_tool,
}

def _text(obj) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(obj, ensure_ascii=False, default=str))]

def build_server(app: AppContext) -> Server:
    """Register the tool list and dispatcher on an MCP server bound to `app`."""
    server = Server("tg-mcp")

    async def _list_tools(_ctx, _params: PaginatedRequestParams | None):
        return ListToolsResult(tools=TOOL_DEFS)

    async def _call_tool(_ctx, params: CallToolRequestParams):
        fn = HANDLERS.get(params.name)
        if fn is None:
            raise ValueError(f"unknown tool: {params.name}")
        return CallToolResult(content=_text(await fn(app, **(params.arguments or {}))))

    server.add_request_handler("tools/list", PaginatedRequestParams, _list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, _call_tool)
    return server

async def _amain():
    settings = load_settings()
    conn = db.connect(settings.tg_mcp_db_path); db.migrate(conn)

    async with TelethonTelegram(settings.tg_api_id, settings.tg_api_hash,
                                 settings.tg_session_path) as tg:
        # list_chats also warms Telethon's entity cache for iter_messages.
        known = {c["chat_id"]: c for c in await tg.list_chats()}
        for cid in settings.watchlist_default:
            if cid in known:
                db.upsert_chat(conn, cid, known[cid]["title"], known[cid]["kind"], True)
            elif conn.execute("SELECT 1 FROM chats WHERE chat_id=?", (cid,)).fetchone():
                db.set_watched(conn, cid, True)  # keep stored title/kind
            else:
                print(f"tg-mcp: watchlist chat {cid} not found in dialogs; "
                      "using placeholder title", file=sys.stderr)
                db.upsert_chat(conn, cid, f"chat:{cid}", "group", True)
        notion = NotionSink(settings.notion_token, settings.notion_database_id)
        sched = None
        try:
            claude = AnthropicClaude(settings.anthropic_api_key, settings.summary_model)
            app = AppContext(conn=conn, tg=tg, claude=claude, notion=notion, settings=settings)
            sched = start_scheduler(app)
            server = build_server(app)
            async with stdio_server() as (r, w):
                await server.run(r, w, server.create_initialization_options())
        finally:
            if sched is not None:
                sched.shutdown(wait=False)
            await notion.close()

def main():
    asyncio.run(_amain())

if __name__ == "__main__":
    main()
