"""Telegram Application factory.

Creates the PTB Application, wires all handlers, registers the scheduler,
and sets up the post_shutdown hook via the builder (PTB v21 requirement).
"""

from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.handlers import (
    BTN_NOW,
    BTN_TOMORROW,
    cmd_help,
    cmd_report,
    cmd_start,
    cmd_status,
    cmd_tomorrow,
    handle_button,
)
from config.settings import Settings
from scheduler.jobs import scheduled_report
from services.pipeline import ReportPipeline
from utils.http import close_session

logger = logging.getLogger(__name__)


async def _on_shutdown(app: Application) -> None:  # type: ignore[type-arg]
    """Close the shared aiohttp session on bot shutdown."""
    await close_session()
    logger.info("HTTP session closed.")


def create_application(settings: Settings) -> Application:  # type: ignore[type-arg]
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_shutdown(_on_shutdown)
        .build()
    )

    # Shared state stored in bot_data so handlers and jobs can access it
    app.bot_data["pipeline"] = ReportPipeline()
    app.bot_data["chat_id"] = settings.telegram_chat_id

    # ── Commands ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("report",   cmd_report))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("status",   cmd_status))

    # ── Keyboard buttons ──────────────────────────────────────────────
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(f"^({BTN_NOW}|{BTN_TOMORROW})$"),
            handle_button,
        )
    )

    # ── Auto-report scheduler ─────────────────────────────────────────
    if settings.report_interval_hours > 0:
        app.job_queue.run_repeating(
            scheduled_report,
            interval=settings.report_interval_hours * 3600,
            first=60,          # first run 60s after startup
            name="auto_report",
        )
        logger.info("Auto-report every %dh.", settings.report_interval_hours)

    logger.info("Application ready (chat_id=%s).", settings.telegram_chat_id)
    return app
