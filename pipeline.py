"""Report pipeline — orchestrates fetch → analyse → format.

  now_report()       → "Next 3 Hours" (from cache)
  today_report()     → full-day today (from cache)
  today_tgv_report() → all TGVs today Paris → Gare Centrale (from cache)

Schedule cache is refreshed every 10 min in background; reports read from cache.
"""

from __future__ import annotations

import logging

from models import Arrival
from analyzer import build_fullday_report, build_now_report
from cache import ScheduleCache
from flights import FlightDataSource
from formatter import format_next_tgv, format_now_report, format_today_report, format_today_tgv
from trains import TrainDataSource

logger = logging.getLogger(__name__)


class ReportPipeline:

    def __init__(self) -> None:
        self._flights = FlightDataSource()
        self._trains  = TrainDataSource()
        self._schedule_cache = ScheduleCache()

    async def refresh_cache(self) -> None:
        """Refresh schedule cache (flights + trains today). Called every 10 min and on first use."""
        await self._schedule_cache.refresh(self._flights, self._trains)

    async def now_report(self) -> str:
        if not self._schedule_cache.is_ready():
            await self.refresh_cache()
        flights, flights_ok = self._schedule_cache.get_flights()
        trains,  trains_ok  = self._schedule_cache.get_trains()
        tgv = self._schedule_cache.get_next_tgv()
        if tgv is None:
            tgv_res = await self._trains.get_next_tgv()
            tgv = tgv_res if isinstance(tgv_res, Arrival) else None
        report = build_now_report(flights, trains, flights_ok=flights_ok, trains_ok=trains_ok)
        return format_now_report(report) + format_next_tgv(tgv)

    async def today_report(self) -> str:
        if not self._schedule_cache.is_ready():
            await self.refresh_cache()
        from datetime import datetime
        import pytz
        now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
        flights, flights_ok = self._schedule_cache.get_flights()
        trains,  trains_ok  = self._schedule_cache.get_trains()
        tgv = self._schedule_cache.get_next_tgv()
        if tgv is None:
            tgv_res = await self._trains.get_next_tgv()
            tgv = tgv_res if isinstance(tgv_res, Arrival) else None
        report = build_fullday_report(
            flights, trains,
            flights_ok=flights_ok, trains_ok=trains_ok,
            day=now,
        )
        return format_today_report(report) + format_next_tgv(tgv)

    async def today_tgv_report(self) -> str:
        """All TGVs today (Paris → Gare Centrale), from cache."""
        if not self._schedule_cache.is_ready():
            await self.refresh_cache()
        tgvs = self._schedule_cache.get_tgvs_today()
        return format_today_tgv(tgvs)
