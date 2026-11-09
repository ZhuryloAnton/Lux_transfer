"""Telegram command and keyboard-button handlers.

Handlers are deliberately thin: they resolve the pipeline, ask
:mod:`taxibot.views` what to say, and deliver it. All content decisions live in
the view layer so the scheduled push (``taxibot.jobs``) produces byte-identical
output to the buttons.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pytz
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from taxibot.core.text import split_message
from taxibot.formatters.report import format_flights_page
from taxibot.views import (
    OutboundMessage,
    build_fresh_soon_messages,
    build_soon_messages,
    build_today_messages,
    build_tomorrow_messages,
    ensure_cache,
    page_keyboard,
)

logger = logging.getLogger(__name__)

# ── Keyboard ──────────────────────────────────────────────────────────────────

BTN_SOON  = "🔜 Arrives soon"
BTN_TODAY = "📋 Today schedule"
BTN_UPDATE = "🔄 Update"

KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_SOON, BTN_TODAY], [BTN_UPDATE]],
    resize_keyboard=True,
    one_time_keyboard=False,
)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")


# ── Delivery ──────────────────────────────────────────────────────────────────

async def _deliver(update: Update, messages: list[OutboundMessage]) -> None:
    """Send each built message as a reply.

    The reply keyboard is attached to the last message only. Attaching it to all
    three made Telegram re-render the keyboard three times per request.
    """
    last = len(messages) - 1
    for i, msg in enumerate(messages):
        markup = msg.reply_markup or (KEYBOARD if i == last else None)
        for chunk in split_message(msg.text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=markup)


async def _respond_with(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    builder,
    loading: str,
    failure: str,
) -> None:
    """Shared shape for every view-backed handler: placeholder → build → deliver.

    This replaced three near-identical 45-line handlers. The placeholder exists
    because building a view can await a cold-cache refresh, and Telegram gives no
    feedback in the meantime.
    """
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return

    placeholder = await update.message.reply_text(loading)
    try:
        messages = await builder(pipeline)
    except Exception:
        logger.exception("%s failed", getattr(builder, "__name__", "view"))
        await _safe_delete(placeholder)
        await update.message.reply_text(failure, reply_markup=KEYBOARD)
        return

    await _safe_delete(placeholder)
    await _deliver(update, messages)


async def _safe_delete(message) -> None:
    """Deleting the placeholder is best-effort; never fail a request over it."""
    try:
        await message.delete()
    except Exception:
        logger.debug("Could not delete placeholder message", exc_info=True)


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT Luxembourg</b>\n\n"
        "Taxi demand forecasts for Luxembourg City:\n"
        "  ✈️ Flights — Luxembourg-Findel\n"
        "  🚄 TGV     — Gare Centrale\n\n"
        f"<b>{BTN_SOON}</b> — the next arrivals, at a glance\n"
        f"<b>{BTN_TODAY}</b> — everything still to come today\n"
        f"<b>{BTN_UPDATE}</b> — fetch the latest data right now\n\n"
        "You also get the “arrives soon” view automatically every hour.",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚖 <b>TaxiBOT — Help</b>\n\n"
        f"<b>{BTN_SOON}</b>\n"
        "  The next 20 flights plus upcoming TGVs, with delays.\n\n"
        f"<b>{BTN_TODAY}</b>\n"
        "  Every remaining arrival today — page through with ◀ ▶.\n\n"
        f"<b>{BTN_UPDATE}</b>\n"
        "  Re-downloads flights, TGVs and delays now, then shows Arrives soon.\n\n"
        "<b>Commands</b>\n"
        "  /start     — show the keyboard\n"
        "  /report    — same as Arrives soon\n"
        "  /today     — same as Today schedule\n"
        "  /tomorrow  — tomorrow's schedule\n"
        "  /flights   — flights only\n"
        "  /tgv       — all TGVs today\n"
        "  /next_tgv  — next TGV only\n"
        "  /status    — bot health check\n"
        "  /help      — this message",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(tz=_LUX_TZ)
    pipeline = context.bot_data.get("pipeline")
    cached = "yes" if pipeline is not None and pipeline._cache_has_today() else "no"
    await update.message.reply_text(
        f"✅ <b>TaxiBOT is running</b>\n"
        f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
        f"📡 Flights : lux-airport.lu API\n"
        f"📡 TGV     : Luxembourg GTFS / OpenData\n"
        f"📍 Station : Gare Centrale Luxembourg\n"
        f"💾 Schedule cached: {cached}",
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _respond_with(
        update, context, build_soon_messages,
        "⏳ Checking what's arriving soon…",
        "❌ Could not load arrivals. Please try again in a moment.",
    )


async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _respond_with(
        update, context, build_fresh_soon_messages,
        "🔄 Updating — fetching the latest data…",
        "❌ Could not update. Please try again in a moment.",
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _respond_with(
        update, context, build_today_messages,
        "⏳ Fetching today's schedule…",
        "❌ Could not load today's schedule. Please try again in a moment.",
    )


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _respond_with(
        update, context, build_tomorrow_messages,
        "⏳ Fetching tomorrow's schedule…",
        "❌ Could not load tomorrow's schedule. Please try again in a moment.",
    )


async def cmd_flights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    placeholder = await update.message.reply_text("⏳ Loading flights…")
    try:
        await ensure_cache(pipeline)
        flights, ok = pipeline._schedule_cache.get("flights_today", ([], False))
        text, total_pages = format_flights_page(
            flights, ok, page=0,
            header_title="✈️ <b>Flights — Luxembourg-Findel</b>",
        )
        await _safe_delete(placeholder)
        kb = page_keyboard(0, total_pages, "fl_list")
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb or KEYBOARD)
    except Exception:
        logger.exception("cmd_flights failed")
        await _safe_delete(placeholder)
        await update.message.reply_text(
            "❌ Could not load flights. Please try again in a moment.",
            reply_markup=KEYBOARD,
        )


async def cmd_tgv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _text_report(
        update, context,
        lambda p: p.tgv_schedule_today(),
        "⏳ Loading today's TGV…",
        "❌ Could not load today's TGV. Please try again in a moment.",
    )


async def cmd_next_tgv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _text_report(
        update, context,
        lambda p: p.next_tgv_report(),
        "⏳ Finding next TGV…",
        "❌ Could not find next TGV. Please try again in a moment.",
    )


async def cmd_trains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _text_report(
        update, context,
        lambda p: p.trains_now_report(),
        "⏳ Loading TGV…",
        "❌ Could not load TGV. Please try again in a moment.",
    )


async def _text_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    call,
    loading: str,
    failure: str,
) -> None:
    """For the pipeline methods that already return a finished string."""
    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        await update.message.reply_text("⚠️ Bot not ready yet, please try again.")
        return
    placeholder = await update.message.reply_text(loading)
    try:
        text = await call(pipeline)
        await _safe_delete(placeholder)
        for chunk in split_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=KEYBOARD)
    except Exception:
        logger.exception("text report failed")
        await _safe_delete(placeholder)
        await update.message.reply_text(failure, reply_markup=KEYBOARD)


# ── Keyboard buttons ──────────────────────────────────────────────────────────

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == BTN_SOON:
        await cmd_report(update, context)
    elif text == BTN_TODAY:
        await cmd_today(update, context)
    elif text == BTN_UPDATE:
        await cmd_update(update, context)


# ── Pagination ────────────────────────────────────────────────────────────────

async def _get_flights_for_page(pipeline, cache_key: str, use_tomorrow: bool = False):
    """Read flights from cache, falling back to a live fetch on a cold cache."""
    flights, ok = pipeline._schedule_cache.get(cache_key, ([], False))
    if flights and ok:
        return flights, ok

    logger.info("Page callback: cache miss for %s, fetching live", cache_key)
    try:
        if use_tomorrow:
            flights = await asyncio.wait_for(pipeline._flights.fetch_tomorrow(), timeout=10)
        else:
            flights = await asyncio.wait_for(pipeline._flights.fetch_today(), timeout=10)
        return flights, True
    except Exception:
        logger.warning("Page callback: live fetch failed for %s", cache_key, exc_info=True)
        return [], False


_PAGE_SOURCES = {
    "fl_today":    ("flights_today",    False, "✈️ <b>Today — Luxembourg-Findel</b>"),
    "fl_tomorrow": ("flights_tomorrow", True,  "✈️ <b>Tomorrow — Luxembourg-Findel</b>"),
    "fl_list":     ("flights_today",    False, "✈️ <b>Flights — Luxembourg-Findel</b>"),
}


async def handle_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ◀ Prev / Next ▶ presses. Callback data is "prefix:page"."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        logger.warning("query.answer() failed", exc_info=True)

    parts = (query.data or "").rsplit(":", 1)
    if len(parts) != 2:
        return
    prefix, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        return

    source = _PAGE_SOURCES.get(prefix)
    if source is None:
        return

    pipeline = context.bot_data.get("pipeline")
    if pipeline is None:
        logger.warning("Pagination callback: pipeline is None")
        return

    cache_key, use_tomorrow, title = source
    try:
        flights, ok = await _get_flights_for_page(pipeline, cache_key, use_tomorrow)
        text, total_pages = format_flights_page(flights, ok, page=page, header_title=title)
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=page_keyboard(page, total_pages, prefix),
        )
        logger.info("Pagination: page %d/%d (%s)", page + 1, total_pages, prefix)
    except Exception:
        logger.exception("Pagination callback failed (page=%d, prefix=%s)", page, prefix)
