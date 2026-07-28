"""Message builders: decide WHAT to send, not HOW to send it.

The bug this module exists to kill: the "next 3 hours" content was written twice
— once in ``handlers._handle_now`` (three messages, paginated, sent as replies)
and once in ``pipeline.now_report`` (a single combined string, used by the
3-hourly job). They drifted, so the automatic push and the button showed
different things.

Now both paths call the same builder here and only differ in delivery:

    handlers  →  build_soon_messages()  →  update.message.reply_text(...)
    jobs      →  build_soon_messages()  →  context.bot.send_message(chat_id, ...)

Builders return plain data (:class:`OutboundMessage`), so they are trivially
testable without a Telegram connection.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from taxibot.formatters.report import (
    format_flights_page,
    format_taxi_tip,
    format_tgv_message,
)
from taxibot.models import Arrival

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")

#: How many flights the "Arrives soon" view shows. Deliberately not paginated —
#: it is meant to be one glanceable screen.
SOON_FLIGHT_COUNT = 20

#: Seconds to wait for a cold-cache refresh before answering with what we have.
_CACHE_WARMUP_TIMEOUT = 15


@dataclass
class OutboundMessage:
    """One message to deliver, with optional inline keyboard."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


# ── Pagination ────────────────────────────────────────────────────────────────

def page_keyboard(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup | None:
    """Build the ◀ / ▶ inline keyboard. None when there is only one page."""
    if total_pages <= 1:
        return None
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"{prefix}:{page - 1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"{prefix}:{page + 1}"))
    return InlineKeyboardMarkup([buttons]) if buttons else None


# ── Cache ─────────────────────────────────────────────────────────────────────

async def ensure_cache(pipeline) -> None:
    """Warm the schedule cache if empty, bounded so a slow feed can't hang the reply.

    The timeout matters: without it a stalled upstream feed would leave the user
    staring at "Fetching…" forever, and — because this is awaited inside a
    handler — would hold that handler's slot for as long as the feed took.
    """
    if pipeline._cache_has_today():
        return
    try:
        await asyncio.wait_for(pipeline.refresh_schedule(), timeout=_CACHE_WARMUP_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("refresh_schedule timed out (%ds) — using existing cache", _CACHE_WARMUP_TIMEOUT)
    except Exception:
        logger.exception("refresh_schedule failed while building a view")


# ── Shared selection helpers ──────────────────────────────────────────────────

def _upcoming(arrivals: list[Arrival], now: datetime) -> list[Arrival]:
    """Drop anything already arrived and sort by when it actually lands.

    Re-filtering here is not redundant with the fetch-time filter: the cache is
    only refreshed every 10 minutes, so by the time a user taps a button some of
    those arrivals are in the past.
    """
    return sorted(
        (a for a in arrivals if a.effective_time >= now),
        key=lambda a: a.effective_time,
    )


def _cached(pipeline, key: str) -> tuple[list[Arrival], bool]:
    return pipeline._schedule_cache.get(key, ([], False))


# ── Views ─────────────────────────────────────────────────────────────────────

async def build_soon_messages(pipeline) -> list[OutboundMessage]:
    """"Arrives soon": the next ``SOON_FLIGHT_COUNT`` flights + remaining TGVs + tip.

    No pagination by design — this is the view the 3-hourly push sends, and a
    pushed message with ◀/▶ buttons would go stale as soon as the cache moved on.
    """
    await ensure_cache(pipeline)
    now = datetime.now(tz=_LUX_TZ)

    flights, fl_ok = _cached(pipeline, "flights_today")
    trains, tr_ok = _cached(pipeline, "trains_today")

    upcoming_flights = _upcoming(flights, now)[:SOON_FLIGHT_COUNT]
    upcoming_trains = _upcoming(trains, now)

    # Roll over to tomorrow's early flights when today is exhausted, so a late
    # evening push is not just an empty list.
    if not upcoming_flights:
        tomorrow_flights, tomorrow_ok = _cached(pipeline, "flights_tomorrow")
        if tomorrow_ok and tomorrow_flights:
            upcoming_flights = _upcoming(tomorrow_flights, now)[:SOON_FLIGHT_COUNT]
            fl_ok = True

    flights_text, _ = format_flights_page(
        upcoming_flights,
        fl_ok,
        page=0,
        header_title=f"✈️ <b>Arriving soon — next {SOON_FLIGHT_COUNT} flights</b>",
    )

    tgv_text = format_tgv_message(
        upcoming_trains,
        tr_ok,
        next_tgv=None,
        title="🚄 <b>TGV arriving soon</b>",
    )

    tip_text = format_taxi_tip(upcoming_flights, upcoming_trains, fl_ok, tr_ok)

    return [
        OutboundMessage(flights_text),
        OutboundMessage(tgv_text),
        OutboundMessage(tip_text),
    ]


async def build_today_messages(pipeline) -> list[OutboundMessage]:
    """"Today schedule": every remaining flight today (paginated) + all TGVs + tip."""
    await ensure_cache(pipeline)
    now = datetime.now(tz=_LUX_TZ)

    flights, fl_ok = _cached(pipeline, "flights_today")
    trains, tr_ok = _cached(pipeline, "trains_today")

    upcoming_flights = _upcoming(flights, now)
    upcoming_trains = _upcoming(trains, now)

    flights_text, total_pages = format_flights_page(
        upcoming_flights,
        fl_ok,
        page=0,
        header_title="✈️ <b>Today — Luxembourg-Findel</b>",
    )
    keyboard = page_keyboard(0, total_pages, "fl_today")

    tgv_text = format_tgv_message(
        upcoming_trains,
        tr_ok,
        next_tgv=None,
        title="🚄 <b>TGV today</b>",
    )

    tip_text = format_taxi_tip(upcoming_flights, upcoming_trains, fl_ok, tr_ok)

    return [
        OutboundMessage(flights_text, keyboard),
        OutboundMessage(tgv_text),
        OutboundMessage(tip_text),
    ]


async def build_tomorrow_messages(pipeline) -> list[OutboundMessage]:
    """Kept for the /tomorrow command, which is no longer on the keyboard."""
    await ensure_cache(pipeline)

    flights, fl_ok = _cached(pipeline, "flights_tomorrow")
    trains, tr_ok = _cached(pipeline, "trains_tomorrow")

    flights_text, total_pages = format_flights_page(
        flights, fl_ok, page=0,
        header_title="✈️ <b>Tomorrow — Luxembourg-Findel</b>",
    )
    keyboard = page_keyboard(0, total_pages, "fl_tomorrow")

    tgv_text = format_tgv_message(
        trains, tr_ok, next_tgv=None, title="🚄 <b>TGV tomorrow</b>",
    )

    return [
        OutboundMessage(flights_text, keyboard),
        OutboundMessage(tgv_text),
        OutboundMessage(format_taxi_tip(flights, trains, fl_ok, tr_ok)),
    ]
