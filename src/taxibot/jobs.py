"""Scheduled background jobs."""

from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from taxibot.core.text import split_message
from taxibot.views import build_soon_messages

logger = logging.getLogger(__name__)


async def scheduled_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Push the "arrives soon" view to the configured chat.

    Uses the same builder as the button, so the automatic message and the
    on-demand one can never disagree. It previously called
    ``pipeline.now_report()`` — a different code path with a different format —
    which is why the two had drifted.

    Inline keyboards are dropped here on purpose: a pushed message with ◀ ▶
    buttons goes stale as soon as the schedule cache moves on, and tapping it
    would silently re-render against different data.
    """
    pipeline = context.bot_data.get("pipeline")
    chat_id = context.bot_data.get("chat_id")
    if pipeline is None or not chat_id:
        logger.error("Scheduled report: pipeline or chat_id missing from bot_data.")
        return

    try:
        messages = await build_soon_messages(pipeline)
    except Exception:
        logger.exception("Scheduled report: building the view failed.")
        return

    sent = 0
    for msg in messages:
        for chunk in split_message(msg.text):
            try:
                await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
                sent += 1
            except Exception:
                # One failed send must not abort the rest of the report.
                logger.exception("Scheduled report: send failed for one chunk.")
    logger.info("Scheduled report sent to chat_id=%s (%d messages).", chat_id, sent)


async def refresh_realtime_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh the GTFS-RT delay cache so reports show live delays."""
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        return
    try:
        await pipeline.refresh_realtime()
    except Exception:
        logger.exception("Refresh realtime failed.")


async def refresh_schedule_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pre-download flights and trains for today/tomorrow so replies are fast."""
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        return
    try:
        await pipeline.refresh_schedule()
    except Exception:
        logger.exception("Refresh schedule cache failed.")
