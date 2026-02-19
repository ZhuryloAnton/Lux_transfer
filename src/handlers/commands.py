from __future__ import annotations

import logging

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

BTN_NOW = "📊 Next 3 Hours"
BTN_TOMORROW = "📅 Tomorrow Schedule"
BTN_EVENTS = "🎤 Big Events"

KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_NOW, BTN_TOMORROW], [BTN_EVENTS]],
    resize_keyboard=True,
    one_time_keyboard=False,
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT Luxembourg</b>\n\n"
        "Real-time taxi demand forecasts:\n"
        "  ✈️ Flights — Luxembourg Airport\n"
        "  🚆 Trains — Gare Centrale\n"
        "  🎤 Events — concerts, festivals, exhibitions\n\n"
        "Tap a button below to get a forecast.",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT Commands</b>\n\n"
        f"<b>{BTN_NOW}</b> — flights + trains arriving soon\n"
        f"<b>{BTN_TOMORROW}</b> — tomorrow train schedule + morning flights\n"
        f"<b>{BTN_EVENTS}</b> — major events today & tomorrow\n\n"
        "/start — show keyboard\n"
        "/report — same as Next 3 Hours\n"
        "/tomorrow — same as Tomorrow Schedule\n"
        "/events — same as Big Events\n"
        "/status — bot health check",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_now(update, context)


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_tomorrow(update, context)


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_events(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from datetime import datetime
    import pytz
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    await update.message.reply_text(
        f"✅ <b>TaxiBOT is running</b>\n"
        f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"📡 Flights: lux-airport.lu API\n"
        f"📡 Trains: Luxembourg GTFS (data.public.lu)\n"
        f"📡 Events: LCTO + Rockhal",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == BTN_NOW:
        await _handle_now(update, context)
    elif text == BTN_TOMORROW:
        await _handle_tomorrow(update, context)
    elif text == BTN_EVENTS:
        await _handle_events(update, context)


async def _handle_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if not pipeline:
        await update.message.reply_text("⚠️ Bot not ready yet.")
        return
    await update.message.reply_text("⏳ Fetching live data…")
    try:
        text = await pipeline.now_report()
        for chunk in _split(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("now_report failed")
        await update.message.reply_text("❌ Report failed. Check logs.", reply_markup=KEYBOARD)


async def _handle_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if not pipeline:
        await update.message.reply_text("⚠️ Bot not ready yet.")
        return
    await update.message.reply_text("⏳ Fetching tomorrow's schedule…")
    try:
        text = await pipeline.tomorrow_report()
        for chunk in _split(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("tomorrow_report failed")
        await update.message.reply_text("❌ Report failed. Check logs.", reply_markup=KEYBOARD)


async def _handle_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if not pipeline:
        await update.message.reply_text("⚠️ Bot not ready yet.")
        return
    await update.message.reply_text("⏳ Fetching events…")
    try:
        text = await pipeline.events_report()
        for chunk in _split(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("events_report failed")
        await update.message.reply_text("❌ Report failed. Check logs.", reply_markup=KEYBOARD)


def _split(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
