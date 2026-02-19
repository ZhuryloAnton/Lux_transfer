from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pytz

from src.models import Arrival, TransportType
from src.services.base import BaseDataSource, DataSourceError
from src.utils.cache import cached
from src.utils.http import fetch_json

logger = logging.getLogger(__name__)

LUX_TZ = pytz.timezone("Europe/Luxembourg")

FLIGHT_API = "https://luxair-flightdata-1.azurewebsites.net/api/v1/Flights"

LANDED_CODES = frozenset({"AR", "LD"})
CANCELLED_CODES = frozenset({"CX", "DL"})


class FlightDataSource(BaseDataSource):
    """Luxembourg Airport arrivals via the official lux-airport.lu API.

    This is the same endpoint the airport website uses to display its
    live arrivals board.  Returns real scheduled, estimated, and landed
    flights with airline, flight number, origin city, and status.
    """

    def __init__(self) -> None:
        super().__init__("flights")

    async def _fetch_day(self, day_str: str) -> list[dict]:
        """Fetch all arrivals for a given day (YYYY-MM-DD)."""
        headers = {
            "Origin": "https://www.lux-airport.lu",
            "Referer": "https://www.lux-airport.lu/en/flights/arrivals/",
        }
        try:
            data = await fetch_json(
                FLIGHT_API,
                params={"Day": day_str, "Sens": "A", "updateMarker": "0"},
                headers=headers,
                ssl=False,
            )
        except Exception as exc:
            raise DataSourceError(f"Luxembourg Airport API unreachable: {exc}") from exc

        if not isinstance(data, dict) or "flights" not in data:
            raise DataSourceError("Airport API returned unexpected format")

        flights = data["flights"]
        if not isinstance(flights, list):
            raise DataSourceError("Airport API flights is not a list")
        return flights

    @cached("flights_today")
    async def fetch_raw(self) -> Any:
        now = datetime.now(tz=LUX_TZ)
        today = now.strftime("%Y-%m-%d")
        raw = await self._fetch_day(today)
        if now.hour >= 21:
            tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                raw += await self._fetch_day(tomorrow)
            except DataSourceError:
                pass
        return raw

    async def fetch_tomorrow_raw(self) -> list[dict]:
        tomorrow = (datetime.now(tz=LUX_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        return await self._fetch_day(tomorrow)

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Get tomorrow's arrivals — all day."""
        try:
            raw = await self.fetch_tomorrow_raw()
        except DataSourceError:
            return []
        parsed = await self.parse(raw)
        return parsed

    async def fetch_tomorrow_morning(self) -> list[Arrival]:
        """Get tomorrow's arrivals — morning only (before 12:00)."""
        try:
            raw = await self.fetch_tomorrow_raw()
        except DataSourceError:
            return []
        parsed = await self.parse(raw)
        noon = (datetime.now(tz=LUX_TZ) + timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0,
        )
        return [a for a in parsed if a.effective_time < noon]

    async def parse(self, raw: Any) -> list[Arrival]:
        if not isinstance(raw, list):
            return []
        arrivals: list[Arrival] = []
        for entry in raw:
            a = self._parse_entry(entry)
            if a is not None:
                arrivals.append(a)
        return arrivals

    async def validate(self, items: list[Arrival]) -> list[Arrival]:
        """Keep future flights — not yet landed, not cancelled."""
        now = datetime.now(tz=LUX_TZ)
        return sorted(
            [
                a for a in items
                if a.effective_time >= now
                and a.status not in CANCELLED_CODES
                and a.status not in LANDED_CODES
            ],
            key=lambda a: a.effective_time,
        )

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

        est_str = entry.get("timeEstimated") or ""
        delay = 0
        if est_str:
            try:
                est = LUX_TZ.localize(datetime.fromisoformat(est_str))
                delay = int((est - sched).total_seconds() / 60)
            except (ValueError, TypeError):
                pass

        airline = entry.get("airline", "")
        iata = entry.get("iataAirCode", "")
        num = entry.get("flightNum", "")
        identifier = f"{iata}{num}" if iata and num else (airline or "Unknown")

        stops = entry.get("stops", {})
        if isinstance(stops, dict) and stops:
            first_key = next(iter(stops))
            origin = f"{stops[first_key]} ({first_key})"
        else:
            origin = "Unknown"

        status = entry.get("statusCode", "")

        return Arrival(
            transport_type=TransportType.FLIGHT,
            scheduled_time=sched,
            identifier=identifier,
            origin=origin,
            status=status,
            delay_minutes=max(delay, 0),
        )
