"""Luxembourg Airport flight arrivals — lux-airport.lu official API.

The airport website fetches from:
  GET https://luxair-flightdata-1.azurewebsites.net/api/v1/Flights
    ?Day=YYYY-MM-DD&Sens=A&updateMarker=0

Response shape: { "flights": [ {...}, ... ] }

Key fields per entry:
  schDate         — scheduled arrival (ISO string, may be naive or tz-aware)
  timeEstimated   — estimated arrival (ISO string, optional)
  statusCode      — "SC"=scheduled, "LD"=landed, "AR"=arrived, "CX"=cancelled, "DL"=delayed
  flightNum       — flight number
  iataAirCode     — airline IATA code
  airline         — airline name
  stops           — dict of { IATA_airport_code: city_name } for origin stops
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytz

from taxibot.models import Arrival, TransportType
from taxibot.core.http import fetch_json

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")

_API_URL = "https://luxair-flightdata-1.azurewebsites.net/api/v1/Flights"
_HEADERS = {
    "Origin":  "https://www.lux-airport.lu",
    "Referer": "https://www.lux-airport.lu/en/flights/arrivals/",
}

# Flights with these status codes are excluded from the active-arrivals list
_DONE_CODES = frozenset({"AR", "LD"})       # already landed / arrived


# ── Datetime helper ───────────────────────────────────────────────────────────

def _to_lux(value: str | None) -> datetime | None:
    """Parse an ISO datetime string → tz-aware datetime in Europe/Luxembourg.

    Handles both naive strings ("2024-02-20T10:00:00") and tz-aware strings
    ("2024-02-20T10:00:00+01:00").  pytz.localize() must not be called on an
    already-aware datetime — this function handles both cases correctly.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return _LUX_TZ.localize(dt, is_dst=None)
    return dt.astimezone(_LUX_TZ)


def _parse_short_time(value: str | None, ref: datetime) -> datetime | None:
    """Parse a short time string like '23:02' using the date from *ref*.

    The API returns timeEstimated/timeFinal as 'HH:MM' (no date).
    If the parsed time is more than 6 hours before *ref*, assume next day
    (handles midnight crossover).
    """
    if not value:
        return None
    try:
        parts = value.strip().split(":")
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    dt = ref.replace(hour=h, minute=m, second=0, microsecond=0)
    # Handle midnight crossover: estimated 01:00 for a flight scheduled at 23:30
    if dt < ref - timedelta(hours=6):
        dt += timedelta(days=1)
    return dt


# ── Data source ───────────────────────────────────────────────────────────────

class FlightDataSource:
    """Fetch and parse flight arrivals at Luxembourg Airport (ELLX / LUX)."""

    # ── Public interface ──────────────────────────────────────────────────────

    async def fetch_today(self) -> list[Arrival]:
        """Return future arrivals for the rest of today.

        After 21:00 also pulls tomorrow's early-morning flights so the
        3-hour window near midnight is always fully covered.
        """
        now = datetime.now(tz=_LUX_TZ)
        raw = await self._fetch_day(now.strftime("%Y-%m-%d"))

        if now.hour >= 21:
            tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                raw += await self._fetch_day(tomorrow)
            except Exception as exc:
                logger.warning("Airport API: could not fetch early tomorrow: %s", exc)

        return self._filter(raw, after=now)

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Return all arrivals for tomorrow."""
        now = datetime.now(tz=_LUX_TZ)
        tomorrow = now + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        try:
            raw = await self._fetch_day(tomorrow_str)
        except Exception as exc:
            logger.warning("Airport API: could not fetch tomorrow: %s", exc)
            return []
        tomorrow_start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        return self._filter(raw, after=tomorrow_start)

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_day(self, day_str: str) -> list[dict]:
        data = await fetch_json(
            _API_URL,
            params={"Day": day_str, "Sens": "A", "updateMarker": "0"},
            headers=_HEADERS,
            ssl=False,
        )
        if not isinstance(data, dict) or "flights" not in data:
            raise ValueError(f"Unexpected airport API response for {day_str}: {type(data)}")
        flights = data["flights"]
        if not isinstance(flights, list):
            raise ValueError(f"Airport API 'flights' key is not a list for {day_str}")
        return flights

    def _filter(self, raw: list[dict], *, after: datetime) -> list[Arrival]:
        arrivals: list[Arrival] = []
        for entry in raw:
            a = _parse_entry(entry)
            if a is None:
                continue
            if a.effective_time < after:
                continue
            if a.status in _DONE_CODES:
                continue
            arrivals.append(a)
        return sorted(arrivals, key=lambda x: x.effective_time)


def _parse_entry(entry: dict) -> Arrival | None:
    if not isinstance(entry, dict):
        return None

    sched = _to_lux(entry.get("schDate") or "")
    if sched is None:
        return None

    # Delay = difference between estimated and scheduled time.
    # timeEstimated comes as short "HH:MM" (not full ISO), so parse with date from schDate.
    delay = 0
    est = _parse_short_time(entry.get("timeEstimated") or "", sched)
    if est is not None:
        delay = max(0, int((est - sched).total_seconds() / 60))

    iata    = (entry.get("iataAirCode") or "").strip()
    num     = (entry.get("flightNum")   or "").strip()
    airline = (entry.get("airline")     or "").strip()
    identifier = f"{iata}{num}" if iata and num else (airline or "Unknown")

    # Origin: first stop in the stops dict — format: { "BRU": "Brussels" }
    stops = entry.get("stops") or {}
    if isinstance(stops, dict) and stops:
        iata_code, city = next(iter(stops.items()))
        origin = f"{city} ({iata_code})"
    else:
        origin = "Unknown"

    status = (entry.get("statusCode") or "").strip().upper()

    return Arrival(
        transport_type=TransportType.FLIGHT,
        scheduled_time=sched,
        identifier=identifier,
        origin=origin,
        status=status,
        delay_minutes=delay,
    )
