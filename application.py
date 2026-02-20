"""Telegram Application factory.

Wires together handlers, scheduler, pipeline, and cache configuration.
"""

from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.handlers import (
    BTN_NOW,
    BTN_TOMORROW,
    KEYBOARD,
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
from utils.cache import configure_cache

logger = logging.getLogger(__name__)


def create_application(settings: Settings) -> Application:
    configure_cache(settings.cache_ttl_seconds)

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    pipeline = ReportPipeline()
    app.bot_data["pipeline"] = pipeline
    app.bot_data["chat_id"] = settings.telegram_chat_id

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("status", cmd_status))

    # Button handler — match exactly the two button labels
    btn_pattern = f"^({BTN_NOW}|{BTN_TOMORROW})$"
    app.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(btn_pattern), handle_button)
    )

    # Auto-report scheduler
    if settings.report_interval_hours > 0:
        interval_seconds = settings.report_interval_hours * 3600
        app.job_queue.run_repeating(
            scheduled_report,
            interval=interval_seconds,
            first=30,
            name="auto_report",
        )
        logger.info("Auto-report scheduled every %dh", settings.report_interval_hours)

    logger.info("Application ready — chat_id=%s", settings.telegram_chat_id)
    return app
