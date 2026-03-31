"""Telegram Application factory.

Creates the PTB Application, wires all handlers, registers the scheduler,
and sets up the post_shutdown hook via the builder (PTB v21 requirement).
"""

from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from handlers import (
    BTN_NOW,
    BTN_TODAY,
    BTN_TODAY_TGV,
    cmd_help,
    cmd_report,
    cmd_start,
    cmd_status,
    cmd_today,
    cmd_today_tgv,
    handle_button,
)
from settings import Settings
from jobs import refresh_schedule_job, scheduled_report
from pipeline import ReportPipeline
from http_client import close_session

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
    app.add_handler(CommandHandler("today",    cmd_today))
    app.add_handler(CommandHandler("today_tgv", cmd_today_tgv))
    app.add_handler(CommandHandler("status",   cmd_status))

    # ── Keyboard buttons ──────────────────────────────────────────────
    btn_pattern = f"^({BTN_NOW}|{BTN_TODAY}|{BTN_TODAY_TGV})$"
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(btn_pattern),
            handle_button,
        )
    )

    # ── Schedule cache: refresh every 10 min (first run 30s after startup)
    app.job_queue.run_repeating(
        refresh_schedule_job,
        interval=600,   # 10 minutes
        first=30,       # warm cache 30s after startup
        name="refresh_schedule",
    )
    logger.info("Schedule cache refresh every 10 min.")

    # ── Auto-report scheduler ─────────────────────────────────────────
    if settings.report_interval_hours > 0:
        app.job_queue.run_repeating(
            scheduled_report,
            interval=settings.report_interval_hours * 3600,
            first=60,
            name="auto_report",
        )
        logger.info("Auto-report every %dh.", settings.report_interval_hours)

    logger.info("Application ready (chat_id=%s).", settings.telegram_chat_id)
    return app
