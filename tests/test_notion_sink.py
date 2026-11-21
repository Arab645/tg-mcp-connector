# tests/test_notion_sink.py
import httpx, pytest, json
from tg_mcp.storage.notion import NotionSink

QUERY_URL_SUFFIX = "/databases/db1/query"

@pytest.mark.asyncio
async def test_creates_when_missing():
    calls = []
    async def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.url.path.endswith(QUERY_URL_SUFFIX):
            return httpx.Response(200, json={"results": []})
        if req.url.path == "/v1/pages" and req.method == "POST":
            return httpx.Response(200, json={"id": "new-page-id"})
        return httpx.Response(500)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.notion.com")
    sink = NotionSink("tkn", "db1", http=client)
    pid = await sink.upsert_summary_page(1, "Alice", "2026-11-21",
                                         {"topics":[{"title":"t","summary":"s"}]}, 5)
    assert pid == "new-page-id"
    assert ("POST","/v1/pages") in calls

@pytest.mark.asyncio
async def test_updates_when_found():
    async def handler(req):
        if req.url.path.endswith(QUERY_URL_SUFFIX):
            return httpx.Response(200, json={"results":[{"id":"existing-id"}]})
        if req.url.path == "/v1/blocks/existing-id/children" and req.method == "GET":
            return httpx.Response(200, json={"results":[], "has_more": False})
        if req.url.path == "/v1/blocks/existing-id/children" and req.method == "PATCH":
            return httpx.Response(200, json={})
        if req.url.path == "/v1/pages/existing-id" and req.method == "PATCH":
            return httpx.Response(200, json={"id":"existing-id"})
        return httpx.Response(500, text=req.url.path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.notion.com")
    sink = NotionSink("tkn", "db1", http=client)
    pid = await sink.upsert_summary_page(1, "Alice", "2026-11-21", {"topics":[]}, 0)
    assert pid == "existing-id"
