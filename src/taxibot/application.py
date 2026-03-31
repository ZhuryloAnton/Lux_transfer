"""Telegram Application factory."""

from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from taxibot.core.config import Settings
from taxibot.core.http import close_session
from taxibot.handlers import (
    BTN_FLIGHTS,
    BTN_NEXT_TGV,
    BTN_NOW,
    BTN_TGV_TODAY,
    BTN_TODAY,
    BTN_TOMORROW,
    cmd_flights,
    cmd_help,
    cmd_next_tgv,
    cmd_report,
    cmd_start,
    cmd_status,
    cmd_tgv,
    cmd_trains,
    cmd_today,
    cmd_tomorrow,
    handle_button,
)
from taxibot.jobs import refresh_realtime_job, refresh_schedule_job, scheduled_report
from taxibot.services import ReportPipeline

logger = logging.getLogger(__name__)


async def _on_shutdown(app: Application) -> None:
    await close_session()
    logger.info("HTTP session closed.")


def create_application(settings: Settings) -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_shutdown(_on_shutdown)
        .build()
    )
    app.bot_data["pipeline"] = ReportPipeline(
        open_data_api=settings.open_data_api,
        gtfs_url=settings.gtfs_url,
        gtfs_rt_url=settings.gtfs_rt_url,
        realtime_refresh_seconds=settings.realtime_refresh_seconds,
    )
    app.bot_data["chat_id"] = settings.telegram_chat_id

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("flights", cmd_flights))
    app.add_handler(CommandHandler("next_tgv", cmd_next_tgv))
    app.add_handler(CommandHandler("trains", cmd_trains))
    app.add_handler(CommandHandler("tgv", cmd_tgv))
    app.add_handler(CommandHandler("status", cmd_status))

    btn_pattern = f"^({BTN_NOW}|{BTN_TODAY}|{BTN_TOMORROW}|{BTN_FLIGHTS}|{BTN_NEXT_TGV}|{BTN_TGV_TODAY})$"
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(btn_pattern),
            handle_button,
        )
    )

    if settings.report_interval_hours > 0:
        app.job_queue.run_repeating(
            scheduled_report,
            interval=settings.report_interval_hours * 3600,
            first=60,
            name="auto_report",
        )
        logger.info("Auto-report every %dh.", settings.report_interval_hours)

    # Refresh real-time train delays every 10 min (GTFS-RT)
    app.job_queue.run_repeating(
        refresh_realtime_job,
        interval=settings.realtime_refresh_seconds,
        first=30,
        name="refresh_realtime",
    )
    logger.info("Real-time delays refresh every %ds.", settings.realtime_refresh_seconds)

    # Pre-download and refresh schedule (flights + trains) every 10 min for fast responses
    app.job_queue.run_repeating(
        refresh_schedule_job,
        interval=settings.realtime_refresh_seconds,
        first=15,
        name="refresh_schedule",
    )
    logger.info("Schedule cache refresh every %ds.", settings.realtime_refresh_seconds)

    logger.info("Application ready (chat_id=%s).", settings.telegram_chat_id)
    return app
