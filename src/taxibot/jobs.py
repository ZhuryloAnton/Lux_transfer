"""Scheduled auto-report job."""

from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from taxibot.core.text import split_message

logger = logging.getLogger(__name__)


async def scheduled_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    chat_id = context.bot_data.get("chat_id")
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


async def refresh_realtime_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh GTFS-RT delay cache every 10 min so reports show live delays."""
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        return
    try:
        await pipeline.refresh_realtime()
    except Exception:
        logger.exception("Refresh realtime failed.")


async def refresh_schedule_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh schedule cache (flights + trains today/tomorrow) every 10 min. Silent background update."""
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        return
    try:
        await pipeline.refresh_schedule()
    except Exception:
        logger.exception("Refresh schedule cache failed.")
