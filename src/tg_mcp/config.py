# src/tg_mcp/config.py
from pathlib import Path
from typing import Annotated, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tg_api_id: int
    tg_api_hash: str
    tg_session_path: Path
    anthropic_api_key: str
    summary_model: str = "claude-haiku-5-5"
    notion_token: str
    notion_database_id: str
    tg_mcp_db_path: Path
    sync_interval_min: int = 15
    max_backfill_days: int = 7
    watchlist_default: Annotated[List[int], NoDecode] = Field(default_factory=list)

    @field_validator("watchlist_default", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v

    @field_validator("tg_session_path", "tg_mcp_db_path")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return Path(str(v)).expanduser()

def load_settings() -> Settings:
    return Settings()
