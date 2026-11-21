# tests/test_telegram_fake.py
import pytest
from tg_mcp.telegram.client import FakeTelegram

@pytest.mark.asyncio
async def test_fake_list_chats_and_iter():
    fake = FakeTelegram(
        chats=[{"chat_id":1,"title":"Alice","kind":"dm"},
               {"chat_id":2,"title":"Team","kind":"group"}],
        messages={
            1: [{"chat_id":1,"message_id":10,"sender_id":1,"sender_name":"a",
                 "date_utc":"2026-11-21T09:00:00+00:00","text":"hi","media_kind":None,
                 "reply_to":None,"raw_json":"{}"}],
            2: [{"chat_id":2,"message_id":100,"sender_id":2,"sender_name":"b",
                 "date_utc":"2026-11-21T10:00:00+00:00","text":"deploy","media_kind":None,
                 "reply_to":None,"raw_json":"{}"}]})
    assert len(await fake.list_chats()) == 2
    out = [m async for m in fake.iter_new_messages(1, min_id=None)]
    assert len(out) == 1 and out[0]["message_id"] == 10
    out = [m async for m in fake.iter_new_messages(1, min_id=10)]
    assert out == []
