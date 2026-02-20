"""Application settings — loaded once from .env, cached for the process lifetime."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required
    telegram_bot_token: str
    telegram_chat_id: str

    # Optional with sensible defaults
    report_interval_hours: int = 3   # 0 = disable auto-reports
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
