"""Message builders: decide WHAT to send, not HOW to send it.

The bug this module exists to kill: the "next 3 hours" content was written twice
— once in ``handlers._handle_now`` (three messages, paginated, sent as replies)
and once in ``pipeline.now_report`` (a single combined string, used by the
hourly job). They drifted, so the automatic push and the button showed
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
    format_replacement_location,
    format_taxi_tip,
    format_tgv_message,
)
from taxibot.models import Arrival
from taxibot.services.pipeline import _next_tgv_from_lists

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


# ── Update button ─────────────────────────────────────────────────────────────

#: Callback data of the inline "🔄 Update" button.
UPDATE_CALLBACK = "update"

#: Inline twin of the reply-keyboard Update button. Channels cannot show a reply
#: keyboard at all, so this is the only way to offer Update there; it also rides
#: under the hourly push. Unlike ◀ ▶ it never goes stale — every press re-fetches.
UPDATE_INLINE = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔄 Update", callback_data=UPDATE_CALLBACK)]]
)


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


def _rollover(
    today: list[Arrival],
    tomorrow: list[Arrival],
    now: datetime,
) -> tuple[list[Arrival], bool]:
    """Today's remaining arrivals, or tomorrow's once today is exhausted.

    By late evening there is nothing left today, and an empty board tells a driver
    nothing. Rolling into tomorrow keeps every view answering "what is next"
    rather than "nothing".

    Returns ``(arrivals, rolled_over)``. The flag is not decoration: board lines
    render only ``HH:MM``, so an unlabelled ``06:00`` shown at 23:00 reads as
    today. Callers use it to say "tomorrow" in the header.
    """
    remaining = _upcoming(today, now)
    if remaining:
        return remaining, False
    return _upcoming(tomorrow, now), True


def _suffix(rolled: bool) -> str:
    return " (tomorrow)" if rolled else ""


def _assemble(
    flights_text: str,
    tgv_text: str,
    tip_text: str,
    replacement: list[Arrival],
    pipeline,
    keyboard: InlineKeyboardMarkup | None = None,
) -> list[OutboundMessage]:
    """Flights, TGV, tip — plus a coach-location message only while it applies.

    The location is its own message so it can be forwarded or pinned on its own:
    "where do I go" is a different question from "what is arriving", and a driver
    wants it without the surrounding board.
    """
    messages = [
        OutboundMessage(flights_text, keyboard),
        OutboundMessage(tgv_text),
    ]
    location_text = format_replacement_location(
        replacement, getattr(pipeline, "_replacement_location", {})
    )
    if location_text:
        messages.append(OutboundMessage(location_text))
    messages.append(OutboundMessage(tip_text))
    return messages


# ── Views ─────────────────────────────────────────────────────────────────────

async def build_soon_messages(pipeline) -> list[OutboundMessage]:
    """"Arrives soon": the next ``SOON_FLIGHT_COUNT`` flights + remaining TGVs + tip.

    No pagination by design — this is the view the hourly push sends, and a
    pushed message with ◀/▶ buttons would go stale as soon as the cache moved on.
    """
    await ensure_cache(pipeline)
    now = datetime.now(tz=_LUX_TZ)

    flights, fl_ok = _cached(pipeline, "flights_today")
    trains, tr_ok = _cached(pipeline, "trains_today")
    flights_tomorrow, fl_tom_ok = _cached(pipeline, "flights_tomorrow")
    trains_tomorrow, tr_tom_ok = _cached(pipeline, "trains_tomorrow")

    # Both roll into tomorrow once today runs dry — by 23:00 "nothing" is not a
    # useful answer for someone deciding where to park.
    upcoming_flights, flights_rolled = _rollover(flights, flights_tomorrow, now)
    upcoming_flights = upcoming_flights[:SOON_FLIGHT_COUNT]
    upcoming_trains, trains_rolled = _rollover(trains, trains_tomorrow, now)
    fl_ok = fl_ok or fl_tom_ok
    tr_ok = tr_ok or tr_tom_ok

    flights_text, _ = format_flights_page(
        upcoming_flights,
        fl_ok,
        page=0,
        header_title=(
            f"✈️ <b>Arriving soon — next {SOON_FLIGHT_COUNT} flights"
            f"{_suffix(flights_rolled)}</b>"
        ),
    )

    # next_tgv is the explicit "nothing today, here is the next one" line. Without
    # it the TGV block just says "no TGV" and the driver cannot tell whether that
    # means later tonight, tomorrow morning, or a suspended service.
    # Prefer today/tomorrow — those carry live GTFS-RT delays. Fall back to the
    # feed-wide lookup so a suspension still reports when service resumes.
    next_tgv = _next_tgv_from_lists(trains, trains_tomorrow) or getattr(
        pipeline, "_next_tgv_any_day", None
    )
    replacement, _ = _cached(pipeline, "tgv_replacement")
    tgv_text = format_tgv_message(
        upcoming_trains,
        tr_ok,
        next_tgv=next_tgv,
        replacement=replacement,
        title=f"🚄 <b>TGV arriving soon{_suffix(trains_rolled)}</b>",
    )

    tip_text = format_taxi_tip(upcoming_flights, upcoming_trains, fl_ok, tr_ok)

    return _assemble(flights_text, tgv_text, tip_text, replacement, pipeline)


async def build_fresh_soon_messages(pipeline) -> list[OutboundMessage]:
    """"Update": re-download schedule and live delays now, then the soon view.

    The regular buttons read a cache that can be up to ``realtime_refresh_seconds``
    old. This one forces both refreshes first so the driver gets the latest data
    without waiting for the next background refresh or the hourly push. Bounded
    by the same timeout as the cold-cache warmup — on a slow feed we answer with
    what is cached rather than hang.
    """
    try:
        await asyncio.wait_for(
            _refresh_all(pipeline), timeout=_CACHE_WARMUP_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.warning("forced refresh timed out (%ds) — using existing cache", _CACHE_WARMUP_TIMEOUT)
    except Exception:
        logger.exception("forced refresh failed — using existing cache")
    return await build_soon_messages(pipeline)


async def _refresh_all(pipeline) -> None:
    # Realtime first: refresh_schedule only reloads delays when they are stale,
    # and "Update" must not show a delay that is up to 10 minutes old.
    await pipeline.refresh_realtime()
    await pipeline.refresh_schedule()


async def build_today_messages(pipeline) -> list[OutboundMessage]:
    """"Today schedule": every remaining flight today (paginated) + all TGVs + tip."""
    await ensure_cache(pipeline)
    now = datetime.now(tz=_LUX_TZ)

    flights, fl_ok = _cached(pipeline, "flights_today")
    trains, tr_ok = _cached(pipeline, "trains_today")
    trains_tomorrow, _ = _cached(pipeline, "trains_tomorrow")

    upcoming_flights = _upcoming(flights, now)
    upcoming_trains = _upcoming(trains, now)

    flights_text, total_pages = format_flights_page(
        upcoming_flights,
        fl_ok,
        page=0,
        header_title="✈️ <b>Today — Luxembourg-Findel</b>",
    )
    keyboard = page_keyboard(0, total_pages, "fl_today")

    # "Today" deliberately does not roll its lists into tomorrow — it means today.
    # But the next-TGV line may point at tomorrow, so an empty evening board still
    # tells you when the next one is.
    # Prefer today/tomorrow — those carry live GTFS-RT delays. Fall back to the
    # feed-wide lookup so a suspension still reports when service resumes.
    next_tgv = _next_tgv_from_lists(trains, trains_tomorrow) or getattr(
        pipeline, "_next_tgv_any_day", None
    )
    replacement, _ = _cached(pipeline, "tgv_replacement")
    tgv_text = format_tgv_message(
        upcoming_trains,
        tr_ok,
        next_tgv=next_tgv,
        replacement=replacement,
        title="🚄 <b>TGV today</b>",
    )

    tip_text = format_taxi_tip(upcoming_flights, upcoming_trains, fl_ok, tr_ok)

    return _assemble(flights_text, tgv_text, tip_text, replacement, pipeline, keyboard)


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
