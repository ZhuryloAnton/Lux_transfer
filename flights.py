"""Luxembourg Airport arrivals via the official lux-airport.lu API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pytz

from bot.models import Arrival, TransportType
from utils.http import fetch_json

logger = logging.getLogger(__name__)

LUX_TZ = pytz.timezone("Europe/Luxembourg")

_FLIGHT_API = "https://luxair-flightdata-1.azurewebsites.net/api/v1/Flights"
_AIRPORT_HEADERS = {
    "Origin": "https://www.lux-airport.lu",
    "Referer": "https://www.lux-airport.lu/en/flights/arrivals/",
}

# Status codes from the airport API
_LANDED_CODES = frozenset({"AR", "LD"})
_CANCELLED_CODES = frozenset({"CX", "DL"})


class FlightDataSource:
    """Real flight arrivals at Luxembourg Airport (ELLX / LUX).

    Fetches from the same endpoint the airport's own website uses.
    Never returns mock or fallback data — raises on failure.
    """

    async def _fetch_day(self, day_str: str) -> list[dict]:
        """Fetch all arrivals for a given YYYY-MM-DD date string."""
        data = await fetch_json(
            _FLIGHT_API,
            params={"Day": day_str, "Sens": "A", "updateMarker": "0"},
            headers=_AIRPORT_HEADERS,
            ssl=False,
        )
        if not isinstance(data, dict) or "flights" not in data:
            raise ValueError(f"Unexpected airport API response for {day_str}")
        flights = data["flights"]
        if not isinstance(flights, list):
            raise ValueError(f"Airport API 'flights' is not a list for {day_str}")
        return flights

    async def fetch_today(self) -> list[Arrival]:
        """Future arrivals for the next 3 hours (today, plus tomorrow if late evening)."""
        now = datetime.now(tz=LUX_TZ)
        raw: list[dict] = []

        try:
            raw = await self._fetch_day(now.strftime("%Y-%m-%d"))
        except Exception as exc:
            logger.error("Airport API fetch failed (today): %s", exc)
            return []

        # Near midnight: also pull next day's early flights
        if now.hour >= 21:
            tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                raw += await self._fetch_day(tomorrow_str)
            except Exception as exc:
                logger.warning("Airport API fetch failed (tomorrow early): %s", exc)

        return self._parse_and_filter(raw, after=now)

    async def fetch_tomorrow_morning(self) -> list[Arrival]:
        """All arrivals tomorrow before 12:00."""
        now = datetime.now(tz=LUX_TZ)
        tomorrow = now + timedelta(days=1)
        noon = tomorrow.replace(hour=12, minute=0, second=0, microsecond=0)

        try:
            raw = await self._fetch_day(tomorrow.strftime("%Y-%m-%d"))
        except Exception as exc:
            logger.error("Airport API fetch failed (tomorrow): %s", exc)
            return []

        after = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        return [a for a in self._parse_and_filter(raw, after=after) if a.effective_time < noon]

    def _parse_and_filter(self, raw: list[dict], *, after: datetime) -> list[Arrival]:
        arrivals: list[Arrival] = []
        for entry in raw:
            a = self._parse_entry(entry)
            if a is None:
                continue
            if a.effective_time < after:
                continue
            if a.status.upper() in _CANCELLED_CODES:
                continue
            if a.status.upper() in _LANDED_CODES:
                continue
            arrivals.append(a)
        return sorted(arrivals, key=lambda x: x.effective_time)

    @staticmethod
    def _parse_entry(entry: dict) -> Arrival | None:
        if not isinstance(entry, dict):
            return None

        sch_str = entry.get("schDate")
        if not sch_str:
            return None
        try:
            sched = LUX_TZ.localize(datetime.fromisoformat(sch_str))
        except (ValueError, TypeError):
            return None

        delay = 0
        est_str = entry.get("timeEstimated") or ""
        if est_str:
            try:
                est = LUX_TZ.localize(datetime.fromisoformat(est_str))
                delay = max(0, int((est - sched).total_seconds() / 60))
            except (ValueError, TypeError):
                pass

        iata = (entry.get("iataAirCode") or "").strip()
        num = (entry.get("flightNum") or "").strip()
        airline = (entry.get("airline") or "").strip()
        identifier = f"{iata}{num}" if iata and num else airline or "Unknown"

        stops = entry.get("stops") or {}
        if isinstance(stops, dict) and stops:
            first_key = next(iter(stops))
            origin = f"{stops[first_key]} ({first_key})"
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
