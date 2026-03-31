"""Train arrivals at Gare Centrale from HAFAS stboard.exe (XML scraping).

The REST API (/opendata/apiserver) is broken after server upgrade to v2.52.
The legacy stboard.exe endpoint still works and provides:
  - Real arrivals (boardType=arr) at Gare Centrale
  - Real-time delay data
  - Train type, origin, platform

No API key required for stboard.exe.
"""

from __future__ import annotations

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

    # Parse delay
    delay_minutes = 0
    if delay_str and delay_str != "-":
        try:
            delay_minutes = max(0, int(delay_str))
        except ValueError:
            pass

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
        status="scheduled",
        delay_minutes=delay_minutes,
        paris_departure=paris_dep,
    )


async def _fetch_stboard(max_journeys: int = 80) -> list[Arrival]:
    """Fetch arrivals at Gare Centrale via stboard.exe XML endpoint."""
    params = {
        "boardType": "arr",
        "input": _GARE_CENTRALE_ID,
        "maxJourneys": str(max_journeys),
        "start": "yes",
        "L": "vs_java3",
    }
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
    logger.info("stboard.exe: %d arrivals (%d delayed)", len(arrivals), delayed)
    return arrivals


class OpenDataTrainSource:
    """Train arrivals from HAFAS stboard.exe (legacy XML endpoint).

    Works without API key. Provides real-time delays when available.
    """

    def __init__(self, api_url: str = "") -> None:
        # api_url is kept for interface compatibility but we use stboard.exe directly
        self._api_url = api_url

    async def fetch_today(self) -> list[Arrival]:
        """Trains arriving today (effective_time >= now)."""
        arrivals = await _fetch_stboard(max_journeys=80)
        now = datetime.now(tz=_LUX_TZ)
        today = now.date()
        return [a for a in arrivals if a.effective_time >= now and a.effective_time.date() == today]

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Trains for tomorrow — stboard only shows near-future, so may be empty."""
        arrivals = await _fetch_stboard(max_journeys=100)
        tomorrow = (datetime.now(tz=_LUX_TZ) + timedelta(days=1)).date()
        return [a for a in arrivals if a.effective_time.date() == tomorrow]

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