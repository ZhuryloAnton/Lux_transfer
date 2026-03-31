"""Telegram command and keyboard-button handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from taxibot.core.text import split_message
from taxibot.formatters.report import format_flights_page, format_taxi_tip, format_tgv_message
from taxibot.services.pipeline import _next_tgv_from_lists

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


# ── Pagination helpers ────────────────────────────────────────────────────────

def _page_keyboard(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup | None:
    """Build ◀ / ▶ inline keyboard for pagination. Returns None if only 1 page."""
    if total_pages <= 1:
        return None
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"{prefix}:{page - 1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"{prefix}:{page + 1}"))
    return InlineKeyboardMarkup([buttons]) if buttons else None


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT Luxembourg</b>\n\n"
        "Taxi demand forecasts for Luxembourg City:\n"
        "  ✈️ Flights  — Luxembourg-Findel\n"
        "  🚄 TGV     — Gare Centrale\n\n"
        "Tap a button below to get started.",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT — Help</b>\n\n"
        f"<b>{BTN_NOW}</b>\n"
        "  Flights + TGV arriving in the next 3 hours.\n\n"
        f"<b>{BTN_TODAY}</b>\n"
        "  Full-day overview: flights + TGV.\n\n"
        f"<b>{BTN_TGV_TODAY}</b>\n"
        "  All TGVs today: Paris → Gare Centrale.\n\n"
        "<b>Commands</b>\n"
        "  /start     — show the keyboard\n"
        "  /report    — same as Next 3 Hours\n"
        "  /today     — same as Today Schedule\n"
        "  /tomorrow  — tomorrow's schedule\n"
        "  /flights   — flights only\n"
        "  /tgv       — TGV schedule today\n"
        "  /next_tgv  — next TGV\n"
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
        f"📡 TGV     : Luxembourg GTFS\n"
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


async def handle_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ◀ Prev / Next ▶ inline button presses for pagination."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    # Format: "prefix:page_num"
    parts = data.rsplit(":", 1)
    if len(parts) != 2:
        return
    prefix, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        return

    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        return

    if prefix == "fl_now":
        flights_data = pipeline._schedule_cache.get("flights_today", ([], False))
        all_flights, ok = flights_data
        # Filter to 3-hour window
        now = datetime.now(tz=_LUX_TZ)
        window_end = now + timedelta(hours=3)
        flights_3h = [a for a in all_flights if now <= a.effective_time <= window_end]
        text, total_pages = format_flights_page(
            flights_3h, ok, page=page,
            header_title="✈️ <b>Flights — Next 3 Hours</b>",
        )
    elif prefix == "fl_today":
        flights = pipeline._schedule_cache.get("flights_today", ([], False))
        text, total_pages = format_flights_page(
            flights[0], flights[1], page=page,
            header_title="✈️ <b>Flights — Today</b>",
        )
    elif prefix == "fl_tomorrow":
        flights = pipeline._schedule_cache.get("flights_tomorrow", ([], False))
        text, total_pages = format_flights_page(
            flights[0], flights[1], page=page,
            header_title="✈️ <b>Flights — Tomorrow</b>",
        )
    elif prefix == "fl_list":
        flights = pipeline._schedule_cache.get("flights_today", ([], False))
        text, total_pages = format_flights_page(
            flights[0], flights[1], page=page,
            header_title="✈️ <b>Flights — Luxembourg-Findel</b>",
        )
    else:
        return

    kb = _page_keyboard(page, total_pages, prefix)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        logger.debug("Could not edit paginated message", exc_info=True)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _handle_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    msg = await update.message.reply_text("⏳ Fetching live data…")
    try:
        if not pipeline._cache_has_today():
            await pipeline.refresh_schedule()
        flights_data = pipeline._schedule_cache.get("flights_today", ([], False))
        trains_data = pipeline._schedule_cache.get("trains_today", ([], False))
        flights, fl_ok = flights_data
        trains, tr_ok = trains_data

        # Filter to 3-hour window
        now = datetime.now(tz=_LUX_TZ)
        window_end = now + timedelta(hours=3)
        flights_3h = [a for a in flights if now <= a.effective_time <= window_end]
        trains_3h = [a for a in trains if now <= a.effective_time <= window_end]

        tomorrow_trains = pipeline._schedule_cache.get("trains_tomorrow", ([], False))[0]
        next_tgv = _next_tgv_from_lists(trains, tomorrow_trains)

        await msg.delete()

        # Message 1: Flights (paginated)
        fl_text, total_pages = format_flights_page(
            flights_3h, fl_ok, page=0,
            header_title="✈️ <b>Flights — Next 3 Hours</b>",
        )
        kb = _page_keyboard(0, total_pages, "fl_now")
        await update.message.reply_text(fl_text, parse_mode="HTML", reply_markup=kb or KEYBOARD)

        # Message 2: TGV
        tgv_text = format_tgv_message(
            trains_3h, tr_ok,
            next_tgv=next_tgv,
            title="🚄 <b>TGV — Next 3 Hours</b>",
        )
        await update.message.reply_text(tgv_text, parse_mode="HTML", reply_markup=KEYBOARD)

        # Message 3: Taxi tip
        tip_text = format_taxi_tip(flights_3h, trains_3h, fl_ok, tr_ok)
        await update.message.reply_text(tip_text, parse_mode="HTML", reply_markup=KEYBOARD)

    except Exception:
        logger.exception("now_report failed")
        try:
            await msg.delete()
        except Exception:
            pass
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
        if not pipeline._cache_has_today():
            await pipeline.refresh_schedule()
        flights_data = pipeline._schedule_cache.get("flights_today", ([], False))
        trains_data = pipeline._schedule_cache.get("trains_today", ([], False))
        flights, fl_ok = flights_data
        trains, tr_ok = trains_data

        tomorrow_trains = pipeline._schedule_cache.get("trains_tomorrow", ([], False))[0]
        next_tgv = _next_tgv_from_lists(trains, tomorrow_trains)

        await msg.delete()

        # Message 1: Flights (paginated)
        fl_text, total_pages = format_flights_page(
            flights, fl_ok, page=0,
            header_title="✈️ <b>Flights — Today</b>",
        )
        kb = _page_keyboard(0, total_pages, "fl_today")
        await update.message.reply_text(fl_text, parse_mode="HTML", reply_markup=kb or KEYBOARD)

        # Message 2: TGV
        tgv_text = format_tgv_message(trains, tr_ok, next_tgv=next_tgv)
        await update.message.reply_text(tgv_text, parse_mode="HTML", reply_markup=KEYBOARD)

        # Message 3: Taxi tip
        tip_text = format_taxi_tip(flights, trains, fl_ok, tr_ok)
        await update.message.reply_text(tip_text, parse_mode="HTML", reply_markup=KEYBOARD)

    except Exception:
        logger.exception("today_report failed")
        try:
            await msg.delete()
        except Exception:
            pass
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
        if not pipeline._cache_has_today():
            await pipeline.refresh_schedule()
        flights_data = pipeline._schedule_cache.get("flights_tomorrow", ([], False))
        trains_data = pipeline._schedule_cache.get("trains_tomorrow", ([], False))
        flights, fl_ok = flights_data
        trains, tr_ok = trains_data

        today_trains = pipeline._schedule_cache.get("trains_today", ([], False))[0]
        next_tgv = _next_tgv_from_lists(today_trains, trains)

        await msg.delete()

        # Message 1: Flights (paginated)
        fl_text, total_pages = format_flights_page(
            flights, fl_ok, page=0,
            header_title="✈️ <b>Flights — Tomorrow</b>",
        )
        kb = _page_keyboard(0, total_pages, "fl_tomorrow")
        await update.message.reply_text(fl_text, parse_mode="HTML", reply_markup=kb or KEYBOARD)

        # Message 2: TGV
        tgv_text = format_tgv_message(trains, tr_ok, next_tgv=next_tgv, title="🚄 <b>TGV — Tomorrow</b>")
        await update.message.reply_text(tgv_text, parse_mode="HTML", reply_markup=KEYBOARD)

        # Message 3: Taxi tip
        tip_text = format_taxi_tip(flights, trains, fl_ok, tr_ok)
        await update.message.reply_text(tip_text, parse_mode="HTML", reply_markup=KEYBOARD)

    except Exception:
        logger.exception("tomorrow_report failed")
        try:
            await msg.delete()
        except Exception:
            pass
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
        if not pipeline._cache_has_today():
            await pipeline.refresh_schedule()
        flights_data = pipeline._schedule_cache.get("flights_today", ([], False))
        flights, ok = flights_data
        await msg.delete()
        text, total_pages = format_flights_page(
            flights, ok, page=0,
            header_title="✈️ <b>Flights — Luxembourg-Findel</b>",
        )
        kb = _page_keyboard(0, total_pages, "fl_list")
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb or KEYBOARD)
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
    msg = await update.message.reply_text("⏳ Loading TGV…")
    try:
        text = await pipeline.trains_now_report()
        await msg.delete()
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("trains_now_report failed")
        await msg.delete()
        await update.message.reply_text(
            "❌ Could not load TGV. Please try again in a moment.",
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
