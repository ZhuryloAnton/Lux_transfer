"""Scheduled auto-report job.

Runs every N hours and sends the 'Next 3 Hours' forecast to the
configured chat_id. Uses the shared ReportPipeline from bot_data.
"""

from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from utils.text import split_message

logger = logging.getLogger(__name__)


async def scheduled_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the 3-hour forecast automatically on a repeating timer."""
    pipeline = context.bot_data.get("pipeline")
    chat_id = context.bot_data.get("chat_id")

    if pipeline is None or chat_id is None:
        logger.error("Scheduled report: pipeline or chat_id missing from bot_data")
        return

    try:
        text = await pipeline.now_report()
        for chunk in split_message(text):
            await context.bot.send_message(
                chat_id=chat_id, text=chunk, parse_mode="HTML"
            )
        logger.info("Scheduled report sent to chat_id=%s", chat_id)
    except Exception:
        logger.exception("Scheduled report failed")
