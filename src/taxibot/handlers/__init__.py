"""Telegram command and keyboard-button handlers."""

from __future__ import annotations

import logging
from datetime import datetime

import pytz
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from taxibot.core.text import split_message

logger = logging.getLogger(__name__)

# ── Keyboard ──────────────────────────────────────────────────────────────────

BTN_NOW       = "📊 Next 3 Hours"
BTN_TODAY     = "📋 Today Schedule"
BTN_TOMORROW  = "📅 Tomorrow"
BTN_FLIGHTS   = "✈️ Flights"
BTN_TGV_TODAY = "🚄 Today TGV"
BTN_NEXT_TGV  = "🚄 Next TGV"

KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_NOW, BTN_TODAY],
        [BTN_TGV_TODAY],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT Luxembourg</b>\n\n"
        "Taxi demand forecasts for Luxembourg City:\n"
        "  ✈️ Flights  — Luxembourg-Findel International Airport\n"
        "  🚆 Trains   — Gare Centrale Luxembourg\n\n"
        "Tap a button below to get started.",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT — Help</b>\n\n"
        f"<b>{BTN_NOW}</b>\n"
        "  Flights + trains arriving in the next 3 hours.\n\n"
        f"<b>{BTN_TODAY}</b>\n"
        "  Full-day overview: flights + trains.\n\n"
        f"<b>{BTN_TGV_TODAY}</b>\n"
        "  All TGVs today: Paris → Gare Centrale.\n\n"
        "<b>Commands</b>\n"
        "  /start     — show the keyboard\n"
        "  /report    — same as Next 3 Hours\n"
        "  /today     — same as Today Schedule\n"
        "  /tomorrow  — tomorrow's schedule\n"
        "  /flights   — flights only\n"
        "  /trains    — trains next 3h\n"
        "  /tgv       — TGV schedule today\n"
        "  /next_tgv  — next TGV Paris → Luxembourg\n"
        "  /status    — bot health check\n"
        "  /help      — this message",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_now(update, context)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_today(update, context)


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_tomorrow(update, context)


async def cmd_flights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_flights(update, context)


async def cmd_trains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_trains(update, context)


async def cmd_tgv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_tgv_today(update, context)


async def cmd_next_tgv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_next_tgv(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(tz=_LUX_TZ)
    await update.message.reply_text(
        f"✅ <b>TaxiBOT is running</b>\n"
        f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
        f"📡 Flights : lux-airport.lu API\n"
        f"📡 Trains  : Luxembourg GTFS (data.public.lu)\n"
        f"📍 Station : Gare Centrale Luxembourg",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == BTN_NOW:
        await _handle_now(update, context)
    elif text == BTN_TODAY:
        await _handle_today(update, context)
    elif text == BTN_TOMORROW:
        await _handle_tomorrow(update, context)
    elif text == BTN_FLIGHTS:
        await _handle_flights(update, context)
    elif text == BTN_TGV_TODAY:
        await _handle_tgv_today(update, context)
    elif text == BTN_NEXT_TGV:
        await _handle_next_tgv(update, context)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _handle_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    msg = await update.message.reply_text("⏳ Fetching live data…")
    try:
        text = await pipeline.now_report()
        await msg.delete()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("now_report failed")
        await msg.delete()
        await update.message.reply_text(
            "❌ Could not generate report. Please try again in a moment.",
            reply_markup=KEYBOARD,
        )


async def _handle_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    msg = await update.message.reply_text("⏳ Fetching today's schedule…")
    try:
        text = await pipeline.today_report()
        await msg.delete()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("today_report failed")
        await msg.delete()
        await update.message.reply_text(
            "❌ Could not generate report. Please try again in a moment.",
            reply_markup=KEYBOARD,
        )


async def _handle_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    msg = await update.message.reply_text("⏳ Fetching tomorrow's schedule…")
    try:
        text = await pipeline.tomorrow_report()
        await msg.delete()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("tomorrow_report failed")
        await msg.delete()
        await update.message.reply_text(
            "❌ Could not generate report. Please try again in a moment.",
            reply_markup=KEYBOARD,
        )


async def _handle_flights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    msg = await update.message.reply_text("⏳ Loading flights…")
    try:
        text = await pipeline.flights_report()
        await msg.delete()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("flights_report failed")
        await msg.delete()
        await update.message.reply_text(
            "❌ Could not load flights. Please try again in a moment.",
            reply_markup=KEYBOARD,
        )


async def _handle_trains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    msg = await update.message.reply_text("⏳ Loading trains…")
    try:
        text = await pipeline.trains_now_report()
        await msg.delete()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("trains_now_report failed")
        await msg.delete()
        await update.message.reply_text(
            "❌ Could not load trains. Please try again in a moment.",
            reply_markup=KEYBOARD,
        )


async def _handle_tgv_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    msg = await update.message.reply_text("⏳ Loading today's TGV…")
    try:
        text = await pipeline.tgv_schedule_today()
        await msg.delete()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("tgv_schedule_today failed")
        await msg.delete()
        await update.message.reply_text(
            "❌ Could not load today's TGV. Please try again in a moment.",
            reply_markup=KEYBOARD,
        )


async def _handle_next_tgv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    msg = await update.message.reply_text("⏳ Finding next TGV…")
    try:
        text = await pipeline.next_tgv_report()
        await msg.delete()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("next_tgv_report failed")
        await msg.delete()
        await update.message.reply_text(
            "❌ Could not find next TGV. Please try again in a moment.",
            reply_markup=KEYBOARD,
        )