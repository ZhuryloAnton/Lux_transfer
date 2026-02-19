from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from src.config import Settings
from src.handlers.commands import (
    BTN_EVENTS,
    BTN_NOW,
    BTN_TOMORROW,
    cmd_events,
    cmd_help,
    cmd_report,
    cmd_start,
    cmd_status,
    cmd_tomorrow,
    handle_button,
)
from src.handlers.scheduler import scheduled_report
from src.services.report_pipeline import ReportPipeline
from src.utils.cache import configure_cache

logger = logging.getLogger(__name__)


def create_application(settings: Settings) -> Application:
    configure_cache(settings.cache_ttl_seconds)

    app = Application.builder().token(settings.telegram_bot_token).build()

    pipeline = ReportPipeline()
    app.bot_data["pipeline"] = pipeline
    app.bot_data["chat_id"] = settings.telegram_chat_id

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("status", cmd_status))

    btn_pattern = f"^({BTN_NOW}|{BTN_TOMORROW}|{BTN_EVENTS})$"
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(btn_pattern), handle_button))

    if settings.report_interval_hours > 0:
        interval = settings.report_interval_hours * 3600
        app.job_queue.run_repeating(
            scheduled_report,
            interval=interval,
            first=30,
            name="auto_report",
        )
        logger.info("Auto-report every %dh", settings.report_interval_hours)

    logger.info("Bot ready — chat_id=%s", settings.telegram_chat_id)
    return app
