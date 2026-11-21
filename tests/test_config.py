# tests/test_config.py
from pathlib import Path
import os
from tg_mcp.config import load_settings

def test_loads_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "abc")
    monkeypatch.setenv("TG_SESSION_PATH", str(tmp_path/"s"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("NOTION_TOKEN", "ntn")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db")
    monkeypatch.setenv("TG_MCP_DB_PATH", str(tmp_path/"db.sqlite3"))
    monkeypatch.setenv("WATCHLIST_DEFAULT", "111,222")
    s = load_settings()
    assert s.tg_api_id == 12345
    assert s.sync_interval_min == 15
    assert s.watchlist_default == [111, 222]
    assert s.tg_session_path == tmp_path/"s"
