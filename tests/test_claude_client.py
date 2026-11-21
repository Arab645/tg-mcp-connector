import pytest
from tg_mcp.summarize.claude import FakeClaude
from tg_mcp.summarize.prompts import build_user_prompt, SYSTEM_PROMPT

def test_build_user_prompt_formats_lines():
    msgs = [{"date_utc":"2026-11-21T09:00:00+00:00","sender_name":"a","text":"hi"},
            {"date_utc":"2026-11-21T09:05:00+00:00","sender_name":"b","text":"hello"}]
    p = build_user_prompt("Team", "2026-11-21", msgs)
    assert "Team" in p and "2026-11-21" in p and "a: hi" in p and "b: hello" in p

def test_system_prompt_mentions_json_fields():
    for k in ["topics","decisions","action_items","open_questions","links","participants"]:
        assert k in SYSTEM_PROMPT

@pytest.mark.asyncio
async def test_fake_claude_returns_canned():
    fake = FakeClaude({"topics":[{"title":"t","summary":"s"}],
                       "decisions":[],"action_items":[],"open_questions":[],
                       "links":[],"participants":["a"]})
    out, tin, tout = await fake.summarize("Team","2026-11-21",[{"text":"x"}])
    assert out["topics"][0]["title"] == "t"
    assert tin > 0 and tout > 0
