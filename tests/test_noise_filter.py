# tests/test_noise_filter.py
from tg_mcp.summarize.filter import filter_messages

def _r(**kw):
    base = {"chat_id":1,"message_id":1,"sender_id":1,"sender_name":"a",
            "date_utc":"2026-11-21T10:00:00+00:00","text":None,"media_kind":None,
            "reply_to":None,"raw_json":"{}"}
    base.update(kw); return base

def test_drops_short_acks_and_empty():
    rows = [_r(message_id=1,text="+"), _r(message_id=2,text="ok"),
            _r(message_id=3,text="спс"), _r(message_id=4,text="👍"),
            _r(message_id=5,text=None), _r(message_id=6,text="")]
    kept, dropped = filter_messages(rows)
    assert kept == []
    assert dropped == 6

def test_keeps_substantive():
    rows = [_r(message_id=1,text="deploy зафейлился на миграции"),
            _r(message_id=2,text="")]
    kept, dropped = filter_messages(rows)
    assert len(kept) == 1 and dropped == 1

def test_drops_service_messages():
    rows = [_r(message_id=1,media_kind="MessageActionPinMessage",text=None)]
    kept, _ = filter_messages(rows)
    assert kept == []
