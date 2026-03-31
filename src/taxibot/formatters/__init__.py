"""Format Report objects into Telegram HTML strings."""

from taxibot.formatters.report import (
    format_flights_report,
    format_next_train_report,
    format_next_tgv,
    format_now_report,
    format_tgv_schedule,
    format_today_report,
    format_tomorrow_report,
    format_trains_next_3h,
)

__all__ = [
    "format_flights_report",
    "format_next_train_report",
    "format_next_tgv",
    "format_now_report",
    "format_tgv_schedule",
    "format_today_report",
    "format_tomorrow_report",
    "format_trains_next_3h",
]
