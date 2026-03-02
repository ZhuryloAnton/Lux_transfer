"""Application settings — loaded from .env, cached for the process lifetime."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: from src/taxibot/core/config.py -> core, taxibot, src -> project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _env_file_path() -> Path:
    """Resolve .env path: project root first, then current working directory."""
    for path in (_PROJECT_ROOT / ".env", Path.cwd() / ".env"):
        if path.exists():
            return path
    return _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str
    telegram_chat_id: str
    gtfs_url: str = ""
    gtfs_rt_url: str = ""
    realtime_refresh_seconds: int = 600

    report_interval_hours: int = 3
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        env_path = _env_file_path()
        if not env_path.exists():
            raise SystemExit(
                f"Missing .env file. Copy .env.example to .env in the project root and set:\n"
                f"  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID\n"
                f"Expected path: {env_path}"
            ) from e
        raise SystemExit(
            f"Invalid or incomplete .env at {env_path}. Required: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.\n"
            f"Copy .env.example to .env and fill in your values."
        ) from e
