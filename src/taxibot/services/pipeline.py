"""Report pipeline — orchestrates fetch → analyse → format."""

from __future__ import annotations

import asyncio
import logging

from taxibot.models import Arrival
from taxibot.services.analyzer import (
    build_fullday_report,
    build_now_report,
    build_tomorrow_report,
)
from taxibot.services.flights import FlightDataSource
from taxibot.services.trains_gtfs import GTFSTrainSource
from taxibot.services.trains_realtime import RealtimeDelayCache
from taxibot.formatters import (
    format_flights_report,
    format_next_train_report,
    format_next_tgv,
    format_now_report,
    format_tgv_schedule,
    format_today_report,
    format_tomorrow_report,
)

logger = logging.getLogger(__name__)


class ReportPipeline:
    def __init__(
        self,
        gtfs_url: str = "",
        gtfs_rt_url: str = "",
        realtime_refresh_seconds: int = 600,
    ) -> None:
        self._realtime = RealtimeDelayCache(
            gtfs_rt_url=gtfs_rt_url,
            cache_ttl_seconds=realtime_refresh_seconds,
        )
        self._flights = FlightDataSource()
        self._trains = GTFSTrainSource(
            gtfs_url=gtfs_url,
            get_delay=self._realtime.get_delay_minutes,
        )

    async def _ensure_realtime_fresh(self) -> None:
        """Load real-time delays if cache is stale (so reports show up-to-date delays)."""
        await self._realtime.ensure_fresh()

    async def refresh_realtime(self) -> None:
        """Refresh GTFS-RT delay cache. Call every 10 min from job queue."""
        await self._realtime.refresh()

    async def now_report(self) -> str:
        await self._ensure_realtime_fresh()
        flights_res, trains_res, tgv_res, next_train_res = await asyncio.gather(
            self._flights.fetch_today(),
            self._trains.fetch_today(),
            self._trains.get_next_tgv(),
            self._trains.get_next_train(),
            return_exceptions=True,
        )
        flights, flights_ok = _unpack(flights_res, "flights/today")
        trains, trains_ok = _unpack(trains_res, "trains/today")
        tgv = tgv_res if isinstance(tgv_res, Arrival) else None
        next_train = next_train_res if isinstance(next_train_res, Arrival) else None
        report = build_now_report(flights, trains, flights_ok=flights_ok, trains_ok=trains_ok)
        report.next_train = next_train
        report.next_tgv = tgv
        return format_now_report(report)

    async def today_report(self) -> str:
        import pytz
        from datetime import datetime

        await self._ensure_realtime_fresh()
        now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
        flights_res, trains_res, tgv_res = await asyncio.gather(
            self._flights.fetch_today(),
            self._trains.fetch_today(),
            self._trains.get_next_tgv(),
            return_exceptions=True,
        )
        flights, flights_ok = _unpack(flights_res, "flights/today")
        trains, trains_ok = _unpack(trains_res, "trains/today")
        tgv = tgv_res if isinstance(tgv_res, Arrival) else None
        report = build_fullday_report(
            flights, trains,
            flights_ok=flights_ok, trains_ok=trains_ok,
            day=now,
        )
        return format_today_report(report) + format_next_tgv(tgv)

    async def tomorrow_report(self) -> str:
        await self._ensure_realtime_fresh()
        flights_res, trains_res, tgv_res = await asyncio.gather(
            self._flights.fetch_tomorrow(),
            self._trains.fetch_tomorrow(),
            self._trains.get_next_tgv(),
            return_exceptions=True,
        )
        flights, flights_ok = _unpack(flights_res, "flights/tomorrow")
        trains, trains_ok = _unpack(trains_res, "trains/tomorrow")
        tgv = tgv_res if isinstance(tgv_res, Arrival) else None
        report = build_tomorrow_report(
            flights, trains,
            flights_ok=flights_ok, trains_ok=trains_ok,
        )
        return format_tomorrow_report(report) + format_next_tgv(tgv)

    async def flights_report(self) -> str:
        flights_res = await asyncio.gather(
            self._flights.fetch_today(),
            return_exceptions=True,
        )
        flights, flights_ok = _unpack(flights_res[0], "flights/today")
        return format_flights_report(flights, flights_ok)

    async def tgv_schedule_today(self) -> str:
        """Full daily TGV schedule (Paris → Luxembourg)."""
        await self._ensure_realtime_fresh()
        trains_res = await self._trains.fetch_today()
        trains, _ = _unpack(trains_res, "trains/today")
        tgvs = [a for a in trains if a.identifier == "TGV"]
        tgvs.sort(key=lambda a: a.effective_time)
        return format_tgv_schedule(tgvs, "today")

    async def next_train_report(self) -> str:
        """Single message: next train (any type) whenever it is — today, tomorrow or later."""
        await self._ensure_realtime_fresh()
        next_train = await self._trains.get_next_train()
        return format_next_train_report(next_train)

    async def next_tgv_report(self) -> str:
        """Single message: next TGV Paris → Luxembourg only."""
        await self._ensure_realtime_fresh()
        tgv_res = await self._trains.get_next_tgv()
        tgv = tgv_res if isinstance(tgv_res, Arrival) else None
        msg = format_next_tgv(tgv)
        return msg.strip()


def _unpack(result: object, label: str) -> tuple[list[Arrival], bool]:
    if isinstance(result, list):
        return result, True
    logger.error("%s raised: %s", label, result)
    return [], False
