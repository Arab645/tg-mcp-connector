# src/tg_mcp/telegram/client.py
from __future__ import annotations
import json
from pathlib import Path
from typing import AsyncIterator, Literal, Protocol, TypedDict, runtime_checkable

class ChatInfo(TypedDict):
    chat_id: int
    title: str
    kind: Literal["group","dm","channel"]

class MessageDTO(TypedDict):
    chat_id: int
    message_id: int
    sender_id: int | None
    sender_name: str | None
    date_utc: str
    text: str | None
    media_kind: str | None
    reply_to: int | None
    raw_json: str

@runtime_checkable
class TelegramPort(Protocol):
    async def list_chats(self) -> list[ChatInfo]: ...
    async def iter_new_messages(self, chat_id: int, min_id: int | None,
                                limit: int = 500) -> AsyncIterator[MessageDTO]: ...
    async def send_message(self, chat_id: int, text: str) -> int: ...

class FakeTelegram:
    def __init__(self, chats: list[ChatInfo], messages: dict[int, list[MessageDTO]]):
        self._chats = chats
        self._messages = messages
        self.sent: list[tuple[int,str]] = []

    async def list_chats(self) -> list[ChatInfo]:
        return list(self._chats)

    async def iter_new_messages(self, chat_id, min_id, limit=500):
        for m in sorted(self._messages.get(chat_id, []), key=lambda x: x["message_id"]):
            if min_id is not None and m["message_id"] <= min_id:
                continue
            yield m

    async def send_message(self, chat_id, text) -> int:
        self.sent.append((chat_id, text))
        return len(self.sent)

class TelethonTelegram:
    """Thin wrapper over Telethon. Session file path from settings."""
    def __init__(self, api_id: int, api_hash: str, session_path: Path):
        from telethon import TelegramClient
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = TelegramClient(str(session_path), api_id, api_hash)

    async def __aenter__(self):
        await self._client.start()
        try:
            import os
            # best-effort tighten session file perms
            for p in self._client.session.filename, str(self._client.session.filename)+"-journal":
                if p and Path(p).exists():
                    os.chmod(p, 0o600)
        except Exception:
            pass
        return self

    async def __aexit__(self, *exc):
        await self._client.disconnect()

    async def list_chats(self) -> list[ChatInfo]:
        out: list[ChatInfo] = []
        async for d in self._client.iter_dialogs():
            kind = "dm" if d.is_user else ("channel" if getattr(d.entity, "broadcast", False) else "group")
            out.append({"chat_id": int(d.id), "title": d.name or "(no title)", "kind": kind})
        return out

    async def iter_new_messages(self, chat_id, min_id, limit=500):
        kwargs = {"limit": limit, "reverse": True}
        if min_id is not None:
            kwargs["min_id"] = min_id
        async for msg in self._client.iter_messages(chat_id, **kwargs):
            sender = await msg.get_sender() if msg.sender_id else None
            sender_name = None
            if sender is not None:
                sender_name = getattr(sender, "username", None) or \
                              " ".join(x for x in [getattr(sender,"first_name",None),
                                                   getattr(sender,"last_name",None)] if x) or None
            media_kind = type(msg.media).__name__ if msg.media else None
            yield {
                "chat_id": chat_id,
                "message_id": int(msg.id),
                "sender_id": int(msg.sender_id) if msg.sender_id else None,
                "sender_name": sender_name,
                "date_utc": msg.date.isoformat(),
                "text": msg.message or None,
                "media_kind": media_kind,
                "reply_to": int(msg.reply_to_msg_id) if msg.reply_to_msg_id else None,
                "raw_json": json.dumps(msg.to_dict(), default=str),
            }

    async def send_message(self, chat_id, text) -> int:
        m = await self._client.send_message(chat_id, text)
        return int(m.id)
