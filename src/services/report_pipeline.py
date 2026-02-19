from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pytz

from src.services.analyzer import build_now_report, build_tomorrow_report
from src.services.events import EventDataSource
from src.services.flights import FlightDataSource
from src.services.formatter import format_events_report, format_now_report, format_tomorrow_report
from src.services.trains import TrainDataSource
from src.utils.cache import invalidate_all

logger = logging.getLogger(__name__)

LUX_TZ = pytz.timezone("Europe/Luxembourg")


class ReportPipeline:
    def __init__(self) -> None:
        self.flight_src = FlightDataSource()
        self.train_src = TrainDataSource()
        self.event_src = EventDataSource()

    async def now_report(self) -> str:
        """Next 3 hours: flights + trains."""
        invalidate_all()
        flights_result, trains_result = await asyncio.gather(
            self.flight_src.get_data(),
            self.train_src.get_data(),
            return_exceptions=True,
        )
        flights, flights_ok = _unpack(flights_result, "flights")
        trains, trains_ok = _unpack(trains_result, "trains")
        report = build_now_report(flights, trains, flights_ok, trains_ok)
        return format_now_report(report)

    async def tomorrow_report(self) -> str:
        """Tomorrow: all trains + morning flights."""
        invalidate_all()
        flights_result, trains_result = await asyncio.gather(
            self.flight_src.fetch_tomorrow_morning(),
            self.train_src.fetch_tomorrow(),
            return_exceptions=True,
        )
        flights, flights_ok = _unpack(flights_result, "tomorrow_flights")
        trains, trains_ok = _unpack(trains_result, "tomorrow_trains")
        report = build_tomorrow_report(flights, trains, flights_ok, trains_ok)
        return format_tomorrow_report(report)

    async def events_report(self) -> str:
        """Big events today and tomorrow."""
        try:
            events = await self.event_src.get_today_tomorrow()
        except Exception:
            logger.exception("events_report data fetch failed")
            events = []
        now = datetime.now(tz=LUX_TZ)
        return format_events_report(events, now)


def _unpack(result: list | Exception, label: str) -> tuple[list, bool]:
    if isinstance(result, list):
        return result, True
    logger.error("%s failed: %s", label, result)
    return [], False
