"""Scheduled jobs: schedule cache refresh (every 10 min) and auto-report (every N hours)."""

from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from text import split_message

logger = logging.getLogger(__name__)


async def refresh_schedule_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh schedule cache (flights + trains) every 10 min. Silent background update."""
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        return
    try:
        await pipeline.refresh_cache()
    except Exception:
        logger.exception("Schedule cache refresh job failed")


async def scheduled_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    chat_id  = context.bot_data.get("chat_id")

    if pipeline is None or not chat_id:
        logger.error("Scheduled report: pipeline or chat_id missing from bot_data.")
        return

    try:
        text = await pipeline.now_report()
        for chunk in split_message(text):
            await context.bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode="HTML",
            )
        logger.info("Scheduled report sent to chat_id=%s.", chat_id)
    except Exception:
        logger.exception("Scheduled report failed.")
