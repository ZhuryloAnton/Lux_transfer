"""Report pipeline — orchestrates fetch → analyse → format.

Two public coroutines:
  now_report()      → "Next 3 Hours" message string
  tomorrow_report() → "Tomorrow Schedule" message string

asyncio.gather() runs flights and trains concurrently.
return_exceptions=True means one source failing never kills the other.
"""

from __future__ import annotations

import asyncio
import logging

from models import Arrival
from analyzer import build_now_report, build_tomorrow_report
from flights import FlightDataSource
from formatter import format_next_tgv, format_now_report, format_tomorrow_report
from trains import TrainDataSource

logger = logging.getLogger(__name__)


class ReportPipeline:

    def __init__(self) -> None:
        self._flights = FlightDataSource()
        self._trains  = TrainDataSource()

    async def now_report(self) -> str:
        flights_res, trains_res, tgv_res = await asyncio.gather(
            self._flights.fetch_today(),
            self._trains.fetch_today(),
            self._trains.get_next_tgv(),
            return_exceptions=True,
        )

        flights, flights_ok = _unpack(flights_res, "flights/today")
        trains,  trains_ok  = _unpack(trains_res,  "trains/today")
        tgv = tgv_res if isinstance(tgv_res, Arrival) else None

        report = build_now_report(flights, trains, flights_ok=flights_ok, trains_ok=trains_ok)
        return format_now_report(report) + format_next_tgv(tgv)

    async def tomorrow_report(self) -> str:
        flights_res, trains_res, tgv_res = await asyncio.gather(
            self._flights.fetch_tomorrow_morning(),
            self._trains.fetch_tomorrow(),
            self._trains.get_next_tgv(),
            return_exceptions=True,
        )

        flights, flights_ok = _unpack(flights_res, "flights/tomorrow")
        trains,  trains_ok  = _unpack(trains_res,  "trains/tomorrow")
        tgv = tgv_res if isinstance(tgv_res, Arrival) else None

        report = build_tomorrow_report(flights, trains, flights_ok=flights_ok, trains_ok=trains_ok)
        return format_tomorrow_report(report) + format_next_tgv(tgv)


def _unpack(result: object, label: str) -> tuple[list[Arrival], bool]:
    """Unwrap a gather result.  Returns (data, ok_flag)."""
    if isinstance(result, list):
        return result, True
    logger.error("%s raised: %s", label, result)
    return [], False
