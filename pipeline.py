"""Report pipeline: fetch → analyse → format.

Two public methods:
  - now_report()      → next 3 hours
  - tomorrow_report() → full tomorrow schedule
"""

from __future__ import annotations

import asyncio
import logging

from bot.models import Arrival
from services.analyzer import build_now_report, build_tomorrow_report
from services.flights import FlightDataSource
from services.formatter import format_next_tgv, format_now_report, format_tomorrow_report
from services.trains import TrainDataSource
from utils.cache import invalidate_all

logger = logging.getLogger(__name__)


class ReportPipeline:
    def __init__(self) -> None:
        self._flights = FlightDataSource()
        self._trains = TrainDataSource()

    async def now_report(self) -> str:
        """Generate the 'Next 3 Hours' report."""
        invalidate_all()

        flights_res, trains_res, tgv = await asyncio.gather(
            self._flights.fetch_today(),
            self._trains.fetch_today(),
            self._trains.get_next_tgv(),
            return_exceptions=True,
        )

        flights, flights_ok = _unpack(flights_res, "flights/today")
        trains, trains_ok = _unpack(trains_res, "trains/today")
        tgv_arrival = tgv if isinstance(tgv, Arrival) else None

        report = build_now_report(
            flights, trains, flights_ok=flights_ok, trains_ok=trains_ok
        )
        text = format_now_report(report)
        text += format_next_tgv(tgv_arrival)
        return text

    async def tomorrow_report(self) -> str:
        """Generate the 'Tomorrow Schedule' report."""
        invalidate_all()

        flights_res, trains_res, tgv = await asyncio.gather(
            self._flights.fetch_tomorrow_morning(),
            self._trains.fetch_tomorrow(),
            self._trains.get_next_tgv(),
            return_exceptions=True,
        )

        flights, flights_ok = _unpack(flights_res, "flights/tomorrow")
        trains, trains_ok = _unpack(trains_res, "trains/tomorrow")
        tgv_arrival = tgv if isinstance(tgv, Arrival) else None

        report = build_tomorrow_report(
            flights, trains, flights_ok=flights_ok, trains_ok=trains_ok
        )
        text = format_tomorrow_report(report)
        text += format_next_tgv(tgv_arrival)
        return text


def _unpack(result: object, label: str) -> tuple[list[Arrival], bool]:
    """Unwrap a gather result: return (data, ok_flag)."""
    if isinstance(result, list):
        return result, True
    logger.error("%s fetch raised: %s", label, result)
    return [], False
