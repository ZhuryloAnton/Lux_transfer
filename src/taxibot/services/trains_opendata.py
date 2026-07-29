"""Train arrivals at Gare Centrale from HAFAS stboard.exe (XML scraping).

The REST API (/opendata/apiserver) is broken after server upgrade to v2.52.
The legacy stboard.exe endpoint still works and provides:
  - Real arrivals (boardType=arr) at Gare Centrale
  - Real-time delay data
  - Train type, origin, platform

No API key required for stboard.exe.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

import pytz

from taxibot.core.http import fetch_text
from taxibot.models import Arrival, TransportType

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")

# HAFAS stboard.exe — legacy endpoint that still works
_STBOARD_URL = "https://cdt.hafas.de/bin/stboard.exe/en"
_GARE_CENTRALE_ID = "000200405060"

# TGV Paris mapping
_PARIS_THIONVILLE_MINUTES = 95
_TGV_PARIS_GATEWAYS = frozenset({"Thionville", "Metz"})

# Train types we care about
_TRAIN_TYPES = frozenset({
    "ICE", "TGV", "IC", "EC", "RE", "RB", "TER", "IR", "CRE", "CRN",
})

_JOURNEY_RE = re.compile(r'<Journey ([^/]*)/?>')

# Arrival.is_cancelled tests for "CX", the code the airport feed uses; reusing it
# keeps one cancellation concept across both transport types.
_CANCELLED = "CX"
_CANCEL_TOKENS = frozenset({"cancel", "cancelled", "canceled"})


def _attr(text: str, name: str) -> str:
    """Extract attribute value from XML tag attributes string."""
    m = re.search(rf'{name}="([^"]*)"', text)
    return m.group(1) if m else ""


def _clean_origin(name: str) -> str:
    """Strip common suffixes from station names."""
    for suffix in (", Gare Centrale", ", Gare", ", Hauptbahnhof", ", Hbf", ", Hafenstrasse"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _parse_journey(attrs: str, base_date: datetime) -> Arrival | None:
    """Parse one <Journey .../> XML element into an Arrival."""
    time_str = _attr(attrs, "fpTime")
    date_str = _attr(attrs, "fpDate")
    delay_str = _attr(attrs, "delay")
    prod_str = _attr(attrs, "prod")
    origin = _attr(attrs, "dir")  # for arrivals, 'dir' is where the train came from

    if not time_str or not prod_str:
        return None

    # Parse time
    try:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None

    # Parse date (DD.MM.YY or use base_date)
    if date_str:
        try:
            day_parts = date_str.split(".")
            day = int(day_parts[0])
            month = int(day_parts[1])
            year = int(day_parts[2])
            if year < 100:
                year += 2000
            dt = _LUX_TZ.localize(
                datetime(year, month, day, h, m, 0), is_dst=None
            )
        except (ValueError, IndexError):
            dt = base_date.replace(hour=h, minute=m, second=0, microsecond=0)
    else:
        dt = base_date.replace(hour=h, minute=m, second=0, microsecond=0)

    # Parse delay. HAFAS is loose about this field — values seen on the live board
    # include "0", "3", "+ 5" (sign *and* space), "-" (no data) and "cancel".
    # A bare int() rejects the last two, and the old bare `except ValueError: pass`
    # turned both into "no delay": a genuine 5-minute delay was dropped, and a
    # cancelled train rendered as "✅ On Time" — which sends a driver to meet a
    # train that is not coming.
    delay_minutes = 0
    status = "scheduled"
    normalised = delay_str.replace(" ", "").lstrip("+")
    if normalised.lower() in _CANCEL_TOKENS:
        status = _CANCELLED
    elif normalised and normalised != "-":
        try:
            delay_minutes = max(0, int(normalised))
        except ValueError:
            # Still swallowed, but no longer silent — an unknown token is a feed
            # change we want to find in the logs rather than mistake for on-time.
            logger.debug("stboard: unparsable delay %r", delay_str)

    # Parse train type from prod="TGV 2816#TGV" or "RB  5634#RB"
    prod_parts = prod_str.split("#")
    identifier = prod_parts[0].strip()
    category = prod_parts[1].strip() if len(prod_parts) > 1 else ""

    # Use category as identifier if cleaner (e.g. "TGV" instead of "TGV 2816")
    train_type = category.upper() if category else identifier.split()[0].upper() if identifier else ""
    if train_type not in _TRAIN_TYPES:
        return None

    # Clean origin
    origin_clean = _clean_origin(origin) if origin else "—"

    # TGV Paris mapping
    paris_dep: datetime | None = None
    if train_type == "TGV" and any(gw in origin_clean for gw in _TGV_PARIS_GATEWAYS):
        paris_dep = dt - timedelta(minutes=_PARIS_THIONVILLE_MINUTES)
        origin_clean = f"Paris Est (via {origin_clean})"

    return Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=dt,
        identifier=train_type,
        origin=origin_clean,
        status=status,
        delay_minutes=delay_minutes,
        paris_departure=paris_dep,
    )


# stboard caps a single reply at ~214 journeys regardless of maxJourneys, and one
# window starting at 00:00 therefore stops around 21:00. Two windows cover a full
# day; the second also spills into the following day, so results are always
# filtered by the date we asked for.
_STBOARD_MAX_JOURNEYS = 300
_DAY_WINDOWS = ("00:00", "20:00")


async def _fetch_stboard(
    max_journeys: int = 80,
    *,
    date: str | None = None,
    time: str | None = None,
) -> list[Arrival]:
    """Fetch arrivals at Gare Centrale via the stboard.exe XML endpoint.

    Without *date* this is the live rolling board from "now". With *date*
    (``DD.MM.YY``) and *time* (``HH:MM``) stboard returns that specific window,
    which is the only way to see beyond the rolling board — a plain request can
    never reach tomorrow. Real-time delays are present either way; only future
    dates report ``delay="-"``, which :func:`_parse_journey` already treats as 0.
    """
    params = {
        "boardType": "arr",
        "input": _GARE_CENTRALE_ID,
        "maxJourneys": str(max_journeys),
        "start": "yes",
        "L": "vs_java3",
    }
    if date:
        params["date"] = date
    if time:
        params["time"] = time
    try:
        body = await fetch_text(_STBOARD_URL, params=params, ssl=False)
    except Exception as e:
        logger.warning("stboard.exe fetch failed: %s", e)
        return []

    now = datetime.now(tz=_LUX_TZ)
    base_date = now.replace(second=0, microsecond=0)

    arrivals: list[Arrival] = []
    for m in _JOURNEY_RE.finditer(body):
        a = _parse_journey(m.group(1), base_date)
        if a is not None:
            arrivals.append(a)

    arrivals.sort(key=lambda x: x.effective_time)
    delayed = sum(1 for a in arrivals if a.delay_minutes > 0)
    logger.info(
        "stboard.exe%s: %d arrivals (%d delayed)",
        f" {date} {time}" if date else "",
        len(arrivals), delayed,
    )
    return arrivals


def _arrival_key(a: Arrival) -> tuple[str, str, str]:
    """Identity of one arrival, for de-duplicating overlapping windows."""
    return (a.identifier, a.scheduled_time.isoformat(), a.origin)


async def _fetch_stboard_day(day: datetime) -> list[Arrival]:
    """Every arrival on *day*, by stitching the two windows together.

    Requesting one board and filtering it — what this used to do for tomorrow —
    cannot work: the rolling board only reaches ~12h ahead, so tomorrow was
    always empty except late in the evening. That made ``/tomorrow`` silently
    correct at 23:00 and silently empty at 09:00, which is worse than failing
    outright.
    """
    date_str = day.strftime("%d.%m.%y")
    target = day.date()

    results = await asyncio.gather(
        *(
            _fetch_stboard(_STBOARD_MAX_JOURNEYS, date=date_str, time=window)
            for window in _DAY_WINDOWS
        ),
        return_exceptions=True,
    )

    merged: dict[tuple[str, str, str], Arrival] = {}
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("stboard.exe window failed for %s: %s", date_str, result)
            continue
        for a in result:
            # The 20:00 window runs into the next day — keep only the day asked for.
            if a.scheduled_time.date() != target:
                continue
            merged.setdefault(_arrival_key(a), a)

    arrivals = sorted(merged.values(), key=lambda a: a.effective_time)
    logger.info("stboard.exe %s: %d arrivals for the full day", date_str, len(arrivals))
    return arrivals


class OpenDataTrainSource:
    """Train arrivals from HAFAS stboard.exe (legacy XML endpoint).

    Works without API key. Provides real-time delays when available.
    """

    def __init__(self, api_url: str = "") -> None:
        # api_url is kept for interface compatibility but we use stboard.exe directly
        self._api_url = api_url

    async def fetch_today(self) -> list[Arrival]:
        """Trains still to arrive today.

        Uses the full-day fetch rather than the rolling board: with the old
        ``max_journeys=80`` the reply stopped around midday, so "Today schedule"
        was missing every evening arrival when asked in the morning.
        """
        now = datetime.now(tz=_LUX_TZ)
        arrivals = await _fetch_stboard_day(now)
        return [a for a in arrivals if a.effective_time >= now]

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Every train arriving tomorrow."""
        return await _fetch_stboard_day(datetime.now(tz=_LUX_TZ) + timedelta(days=1))

    async def get_next_train(self) -> Arrival | None:
        """Next train (any type)."""
        arrivals = await _fetch_stboard(max_journeys=20)
        now = datetime.now(tz=_LUX_TZ)
        future = [a for a in arrivals if a.effective_time > now]
        return min(future, key=lambda a: a.effective_time) if future else None

    async def get_next_tgv(self) -> Arrival | None:
        """Next TGV only."""
        arrivals = await _fetch_stboard(max_journeys=80)
        now = datetime.now(tz=_LUX_TZ)
        tgvs = [a for a in arrivals if a.effective_time > now and a.identifier == "TGV"]
        return min(tgvs, key=lambda a: a.effective_time) if tgvs else None