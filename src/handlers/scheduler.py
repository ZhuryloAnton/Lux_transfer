from __future__ import annotations

import logging

from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def scheduled_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-send the 3-hour forecast on a timer."""
    pipeline = context.bot_data.get("pipeline")
    chat_id = context.bot_data.get("chat_id")
    if not pipeline or not chat_id:
        logger.error("Scheduled report: pipeline or chat_id missing")
        return
    try:
        text = await pipeline.now_report()
        for chunk in _split(text):
            await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
        logger.info("Scheduled report sent to %s", chat_id)
    except Exception:
        logger.exception("Scheduled report failed")


def _split(text: str, limit: int = 4000) -> list[str]:
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
