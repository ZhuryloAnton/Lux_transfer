"""Telegram command and button handlers.

Only two buttons: Schedule Now (Next 3 Hours) and Tomorrow Schedule.
All event-related handlers have been removed.
"""

from __future__ import annotations

import logging

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.text import split_message

logger = logging.getLogger(__name__)

BTN_NOW = "📊 Next 3 Hours"
BTN_TOMORROW = "📅 Tomorrow Schedule"

KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_NOW, BTN_TOMORROW]],
    resize_keyboard=True,
    one_time_keyboard=False,
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT Luxembourg</b>\n\n"
        "Real-time taxi demand forecasts:\n"
        "  ✈️ Flights — Luxembourg Airport\n"
        "  🚆 Trains — Gare Centrale Luxembourg\n\n"
        "Tap a button below to get a forecast.",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT Commands</b>\n\n"
        f"<b>{BTN_NOW}</b> — flights + trains arriving in the next 3 hours\n"
        f"<b>{BTN_TOMORROW}</b> — tomorrow's full train schedule + morning flights\n\n"
        "/start — show the keyboard\n"
        "/report — same as Next 3 Hours\n"
        "/tomorrow — same as Tomorrow Schedule\n"
        "/status — bot health check\n"
        "/help — this message",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_now(update, context)


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_tomorrow(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import pytz
    from datetime import datetime
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    await update.message.reply_text(
        f"✅ <b>TaxiBOT is running</b>\n"
        f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"📡 Flights: lux-airport.lu API\n"
        f"📡 Trains: Luxembourg GTFS (data.public.lu — Gare Centrale only)",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == BTN_NOW:
        await _handle_now(update, context)
    elif text == BTN_TOMORROW:
        await _handle_tomorrow(update, context)


# ---------------------------------------------------------------------------
# Internal handlers
# ---------------------------------------------------------------------------


async def _handle_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet.")
        return
    await update.message.reply_text("⏳ Fetching live data…")
    try:
        text = await pipeline.now_report()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("now_report failed")
        await update.message.reply_text(
            "❌ Report generation failed. Please try again.", reply_markup=KEYBOARD
        )


async def _handle_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet.")
        return
    await update.message.reply_text("⏳ Fetching tomorrow's schedule…")
    try:
        text = await pipeline.tomorrow_report()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("tomorrow_report failed")
        await update.message.reply_text(
            "❌ Report generation failed. Please try again.", reply_markup=KEYBOARD
        )
