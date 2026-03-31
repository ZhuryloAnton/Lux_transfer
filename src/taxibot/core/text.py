"""Text helpers shared across handlers, formatter, and scheduler."""

from __future__ import annotations

import html


def escape(value: str) -> str:
    """HTML-escape a dynamic value before embedding in a Telegram HTML message."""
    return html.escape(str(value))


def split_message(text: str, limit: int = 4_000) -> list[str]:
    """Split a long message into Telegram-safe chunks, cutting at newlines."""
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
