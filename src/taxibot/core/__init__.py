"""Core utilities: config, HTTP client, text helpers."""

from taxibot.core.config import Settings, get_settings
from taxibot.core.http import close_session, fetch_bytes, fetch_json
from taxibot.core.text import escape, split_message

__all__ = [
    "Settings",
    "get_settings",
    "close_session",
    "fetch_json",
    "fetch_bytes",
    "escape",
    "split_message",
]
