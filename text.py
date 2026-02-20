"""Text utilities shared across bot handlers and scheduler."""

from __future__ import annotations

import html


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Split a long message into Telegram-safe chunks at newline boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def escape(value: str) -> str:
    """HTML-escape a value before embedding in a Telegram HTML message."""
    return html.escape(value)
