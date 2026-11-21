# src/tg_mcp/storage/notion.py
from __future__ import annotations
import httpx

NOTION_VERSION = "2022-06-28"

def _para(text: str) -> dict:
    return {"object":"block","type":"paragraph",
            "paragraph":{"rich_text":[{"type":"text","text":{"content":text[:1900]}}]}}

def _h2(text: str) -> dict:
    return {"object":"block","type":"heading_2",
            "heading_2":{"rich_text":[{"type":"text","text":{"content":text}}]}}

def _bullet(text: str) -> dict:
    return {"object":"block","type":"bulleted_list_item",
            "bulleted_list_item":{"rich_text":[{"type":"text","text":{"content":text[:1900]}}]}}

def render_blocks(summary: dict, message_count: int) -> list[dict]:
    blocks: list[dict] = [_para(f"Messages considered: {message_count}")]
    for section, key, formatter in [
        ("Topics","topics", lambda t: f"{t.get('title','')}: {t.get('summary','')}"),
        ("Decisions","decisions", str),
        ("Action items","action_items",
            lambda a: f"[{a.get('owner','?')}] {a.get('text','')}"),
        ("Open questions","open_questions", str),
        ("Links","links", lambda l: f"{l.get('url','')} — {l.get('context','')}"),
    ]:
        items = summary.get(key) or []
        if not items: continue
        blocks.append(_h2(section))
        for it in items:
            blocks.append(_bullet(formatter(it) if not isinstance(it, str) else it))
    ps = summary.get("participants") or []
    if ps:
        blocks.append(_h2("Participants"))
        blocks.append(_para(", ".join(ps)))
    return blocks

def _render_digest_blocks(content_md: str) -> list[dict]:
    """Minimal Markdown → Notion blocks: ##/### headers, bullet lines, paragraphs."""
    blocks = []
    for raw in content_md.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("## "):
            blocks.append({"object":"block","type":"heading_2",
                "heading_2":{"rich_text":[{"type":"text","text":{"content": line[3:].strip()}}]}})
        elif line.startswith("### "):
            blocks.append({"object":"block","type":"heading_3",
                "heading_3":{"rich_text":[{"type":"text","text":{"content": line[4:].strip()}}]}})
        elif line.startswith("- "):
            blocks.append({"object":"block","type":"bulleted_list_item",
                "bulleted_list_item":{"rich_text":[{"type":"text","text":{"content": line[2:].strip()[:1900]}}]}})
        else:
            blocks.append({"object":"block","type":"paragraph",
                "paragraph":{"rich_text":[{"type":"text","text":{"content": line[:1900]}}]}})
    return blocks

class NotionSink:
    def __init__(self, token: str, database_id: str, http: httpx.AsyncClient | None = None):
        self._db = database_id
        self._own = http is None
        self._http = http or httpx.AsyncClient(base_url="https://api.notion.com", timeout=30)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def close(self):
        if self._own:
            await self._http.aclose()

    async def _find_page(self, key: str) -> str | None:
        r = await self._http.post(f"/v1/databases/{self._db}/query",
            headers=self._headers,
            json={"filter":{"property":"Key","rich_text":{"equals": key}}})
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0]["id"] if results else None

    async def _clear_children(self, page_id: str) -> None:
        # Fetch and delete existing children so update is idempotent
        r = await self._http.get(f"/v1/blocks/{page_id}/children",
                                  headers=self._headers, params={"page_size": 100})
        r.raise_for_status()
        for b in r.json().get("results", []):
            await self._http.request("DELETE", f"/v1/blocks/{b['id']}", headers=self._headers)

    async def upsert_summary_page(self, chat_id, chat_title, date, summary, message_count) -> str:
        key = f"{chat_id}:{date}"
        title = f"{chat_title} — {date}"
        blocks = render_blocks(summary, message_count)
        props = {
            "Name":{"title":[{"text":{"content": title}}]},
            "Key":{"rich_text":[{"text":{"content": key}}]},
            "Date":{"date":{"start": date}},
            "ChatId":{"number": chat_id},
            "MessageCount":{"number": message_count},
        }
        existing = await self._find_page(key)
        if existing:
            r = await self._http.patch(f"/v1/pages/{existing}",
                                        headers=self._headers, json={"properties": props})
            r.raise_for_status()
            await self._clear_children(existing)
            r = await self._http.patch(f"/v1/blocks/{existing}/children",
                                        headers=self._headers, json={"children": blocks})
            r.raise_for_status()
            return existing
        r = await self._http.post("/v1/pages", headers=self._headers,
                                   json={"parent":{"database_id": self._db},
                                         "properties": props, "children": blocks})
        r.raise_for_status()
        return r.json()["id"]

    async def upsert_digest_page(self, date, content_md, chat_count, total_messages) -> str:
        """Upsert a digest page keyed by `digest:{date}` on property Key."""
        key = f"digest:{date}"
        title = f"Daily digest — {date}"
        blocks = _render_digest_blocks(content_md)
        props = {
            "Name":{"title":[{"text":{"content": title}}]},
            "Key":{"rich_text":[{"text":{"content": key}}]},
            "Date":{"date":{"start": date}},
            "ChatId":{"number": 0},  # 0 == digest sentinel
            "MessageCount":{"number": total_messages},
        }
        existing = await self._find_page(key)
        if existing:
            r = await self._http.patch(f"/v1/pages/{existing}",
                headers=self._headers, json={"properties": props})
            r.raise_for_status()
            await self._clear_children(existing)
            r = await self._http.patch(f"/v1/blocks/{existing}/children",
                headers=self._headers, json={"children": blocks})
            r.raise_for_status()
            return existing
        r = await self._http.post("/v1/pages", headers=self._headers,
            json={"parent":{"database_id": self._db}, "properties": props, "children": blocks})
        r.raise_for_status()
        return r.json()["id"]
