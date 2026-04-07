"""Application settings — loaded from .env + os.environ, cached for process lifetime.

Lightweight replacement for pydantic-settings to reduce memory (~6 MB saved).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Project root: from src/taxibot/core/config.py -> core, taxibot, src -> project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv() -> None:
    """Read .env file into os.environ (only sets keys not already present)."""
    for candidate in (_PROJECT_ROOT / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value
            return


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    v = _env(key)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    open_data_api: str = ""
    gtfs_url: str = ""
    gtfs_rt_url: str = ""
    realtime_refresh_seconds: int = 600
    hafas_api_key: str = ""
    report_interval_hours: int = 3
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()

    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        env_path = _PROJECT_ROOT / ".env"
        raise SystemExit(
            f"Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.\n"
            f"Set them in environment variables or in {env_path}"
        )

    log_level = _env("LOG_LEVEL", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        log_level = "INFO"

    return Settings(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        open_data_api=_env("OPEN_DATA_API"),
        gtfs_url=_env("GTFS_URL"),
        gtfs_rt_url=_env("GTFS_RT_URL"),
        realtime_refresh_seconds=_env_int("REALTIME_REFRESH_SECONDS", 600),
        hafas_api_key=_env("HAFAS_API_KEY"),
        report_interval_hours=_env_int("REPORT_INTERVAL_HOURS", 3),
        log_level=log_level,
    )
