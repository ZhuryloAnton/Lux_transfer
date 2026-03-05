"""Report pipeline — orchestrates fetch → analyse → format."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import pytz

from taxibot.models import Arrival
from taxibot.services.analyzer import (
    build_fullday_report,
    build_now_report,
    build_tomorrow_report,
)
from taxibot.services.flights import FlightDataSource
from taxibot.services.trains_gtfs import GTFSTrainSource
from taxibot.services.trains_opendata import OpenDataTrainSource
from taxibot.services.trains_realtime import RealtimeDelayCache
from taxibot.formatters import (
    format_flights_report,
    format_next_train_report,
    format_next_tgv,
    format_now_report,
    format_tgv_schedule,
    format_today_report,
    format_tomorrow_report,
    format_trains_next_3h,
)

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")


def _next_train_from_lists(today: list[Arrival], tomorrow: list[Arrival]) -> Arrival | None:
    now = datetime.now(tz=_LUX_TZ)
    after = [a for a in today if a.effective_time > now]
    if after:
        return min(after, key=lambda a: a.effective_time)
    if tomorrow:
        return min(tomorrow, key=lambda a: a.effective_time)
    return None


# Search for next TGV in order: today, then tomorrow (cache limit; extend cache to search more days).
_MAX_DAYS_TGV_SEARCH = 2


def _next_tgv_from_lists(today: list[Arrival], tomorrow: list[Arrival]) -> Arrival | None:
    """Find next TGV by searching today then tomorrow until one is found or day limit reached."""
    now = datetime.now(tz=_LUX_TZ)
    for day_list in (today, tomorrow):
        tgvs = [a for a in day_list if "TGV" in (a.identifier or "").upper() and a.effective_time > now]
        if not tgvs and day_list is tomorrow:
            tgvs = [a for a in day_list if "TGV" in (a.identifier or "").upper()]
        if tgvs:
            return min(tgvs, key=lambda a: a.effective_time)
    return None


def _flight_key(a: Arrival) -> tuple[str, str, str]:
    """(identifier, origin, date) to match same flight on same day."""
    return (a.identifier, a.origin, a.scheduled_time.strftime("%Y-%m-%d"))


def _detect_flight_moves(
    old_flights: list[Arrival],
    new_flights: list[Arrival],
    day_label: str,
) -> None:
    """Log when a flight was rescheduled (scheduled_time changed) or dropped from schedule."""
    if not old_flights:
        return
    old_by_key: dict[tuple[str, str, str], datetime] = {
        _flight_key(a): a.scheduled_time for a in old_flights
    }
    new_keys = {_flight_key(a) for a in new_flights}
    for a in new_flights:
        key = _flight_key(a)
        old_sched = old_by_key.get(key)
        if old_sched is not None and old_sched != a.scheduled_time:
            logger.info(
                "Flight moved [%s]: %s from %s — was %s, now %s",
                day_label,
                a.identifier,
                a.origin,
                old_sched.strftime("%H:%M"),
                a.scheduled_time.strftime("%H:%M"),
            )
    for key in old_by_key:
        if key not in new_keys:
            ident, origin, _ = key
            logger.info(
                "Flight no longer in schedule [%s]: %s from %s (cancelled or departed)",
                day_label,
                ident,
                origin,
            )


class ReportPipeline:
    def __init__(
        self,
        open_data_api: str = "",
        gtfs_url: str = "",
        gtfs_rt_url: str = "",
        realtime_refresh_seconds: int = 600,
    ) -> None:
        self._realtime = RealtimeDelayCache(
            gtfs_rt_url=gtfs_rt_url,
            cache_ttl_seconds=realtime_refresh_seconds,
        )
        self._flights = FlightDataSource()
        if open_data_api and open_data_api.strip():
            self._trains = OpenDataTrainSource(api_url=open_data_api.strip())
        else:
            self._trains = GTFSTrainSource(
                gtfs_url=gtfs_url,
                get_delay=self._realtime.get_delay_minutes,
            )
        self._schedule_cache: dict[str, tuple[list[Arrival], bool]] = {}
        self._schedule_lock = asyncio.Lock()

    async def _ensure_realtime_fresh(self) -> None:
        """Load real-time delays if cache is stale (so reports show up-to-date delays)."""
        await self._realtime.ensure_fresh()

    async def refresh_realtime(self) -> None:
        """Refresh GTFS-RT delay cache. Call every 10 min from job queue."""
        await self._realtime.refresh()

    async def refresh_schedule(self) -> None:
        """Pre-download flights and trains for today and tomorrow; update cache. Run every 10 min."""
        await self._ensure_realtime_fresh()
        async with self._schedule_lock:
            old_flights_today = self._schedule_cache.get("flights_today", ([], False))[0]
            old_flights_tomorrow = self._schedule_cache.get("flights_tomorrow", ([], False))[0]
            results = await asyncio.gather(
                self._flights.fetch_today(),
                self._trains.fetch_today(),
                self._flights.fetch_tomorrow(),
                self._trains.fetch_tomorrow(),
                return_exceptions=True,
            )
            new_flights_today, flights_today_ok = _unpack(results[0], "flights/today")
            new_trains_today, trains_today_ok = _unpack(results[1], "trains/today")
            new_flights_tomorrow, flights_tomorrow_ok = _unpack(results[2], "flights/tomorrow")
            new_trains_tomorrow, trains_tomorrow_ok = _unpack(results[3], "trains/tomorrow")
            if flights_today_ok:
                _detect_flight_moves(old_flights_today, new_flights_today, "today")
            if flights_tomorrow_ok:
                _detect_flight_moves(old_flights_tomorrow, new_flights_tomorrow, "tomorrow")
            self._schedule_cache["flights_today"] = (new_flights_today, flights_today_ok)
            self._schedule_cache["trains_today"] = (new_trains_today, trains_today_ok)
            self._schedule_cache["flights_tomorrow"] = (new_flights_tomorrow, flights_tomorrow_ok)
            self._schedule_cache["trains_tomorrow"] = (new_trains_tomorrow, trains_tomorrow_ok)
        logger.debug(
            "Schedule cache updated: %d/%d today, %d/%d tomorrow",
            len(self._schedule_cache.get("flights_today", ([], False))[0]),
            len(self._schedule_cache.get("trains_today", ([], False))[0]),
            len(self._schedule_cache.get("flights_tomorrow", ([], False))[0]),
            len(self._schedule_cache.get("trains_tomorrow", ([], False))[0]),
        )

    def _get_cached_today(self) -> tuple[list[Arrival], bool, list[Arrival], bool]:
        fl, fl_ok = self._schedule_cache.get("flights_today", ([], False))
        tr, tr_ok = self._schedule_cache.get("trains_today", ([], False))
        return fl, fl_ok, tr, tr_ok

    def _get_cached_tomorrow(self) -> tuple[list[Arrival], bool, list[Arrival], bool]:
        fl, fl_ok = self._schedule_cache.get("flights_tomorrow", ([], False))
        tr, tr_ok = self._schedule_cache.get("trains_tomorrow", ([], False))
        return fl, fl_ok, tr, tr_ok

    def _cache_has_today(self) -> bool:
        return "flights_today" in self._schedule_cache and "trains_today" in self._schedule_cache

    async def now_report(self) -> str:
        if not self._cache_has_today():
            await self.refresh_schedule()
        flights, flights_ok, trains, trains_ok = self._get_cached_today()
        next_train = _next_train_from_lists(trains, self._schedule_cache.get("trains_tomorrow", ([], False))[0])
        next_tgv = _next_tgv_from_lists(trains, self._schedule_cache.get("trains_tomorrow", ([], False))[0])
        report = build_now_report(flights, trains, flights_ok=flights_ok, trains_ok=trains_ok)
        report.next_train = next_train
        report.next_tgv = next_tgv
        return format_now_report(report)

    async def trains_now_report(self) -> str:
        """Trains for the next 3 hours only (same content as in Now report, no button)."""
        if not self._cache_has_today():
            await self.refresh_schedule()
        _, _, trains, trains_ok = self._get_cached_today()
        report = build_now_report([], trains, flights_ok=False, trains_ok=trains_ok)
        return format_trains_next_3h(report)

    async def today_report(self) -> str:
        if not self._cache_has_today():
            await self.refresh_schedule()
        flights, flights_ok, trains, trains_ok = self._get_cached_today()
        now = datetime.now(tz=_LUX_TZ)
        tgv = _next_tgv_from_lists(trains, self._schedule_cache.get("trains_tomorrow", ([], False))[0])
        report = build_fullday_report(
            flights, trains,
            flights_ok=flights_ok, trains_ok=trains_ok,
            day=now,
        )
        return format_today_report(report) + format_next_tgv(tgv)

    async def tomorrow_report(self) -> str:
        if not self._cache_has_today():
            await self.refresh_schedule()
        flights, flights_ok, trains, trains_ok = self._get_cached_tomorrow()
        tgv = _next_tgv_from_lists(
            self._schedule_cache.get("trains_today", ([], False))[0],
            trains,
        )
        report = build_tomorrow_report(
            flights, trains,
            flights_ok=flights_ok, trains_ok=trains_ok,
        )
        return format_tomorrow_report(report) + format_next_tgv(tgv)

    async def flights_report(self) -> str:
        if not self._cache_has_today():
            await self.refresh_schedule()
        flights, flights_ok, _, _ = self._get_cached_today()
        return format_flights_report(flights, flights_ok)

    async def tgv_schedule_today(self) -> str:
        if not self._cache_has_today():
            await self.refresh_schedule()
        _, _, trains, _ = self._get_cached_today()
        tgvs = [a for a in trains if "TGV" in (a.identifier or "").upper()]
        tgvs.sort(key=lambda a: a.effective_time)
        return format_tgv_schedule(tgvs, "today")

    async def next_train_report(self) -> str:
        if not self._cache_has_today():
            await self.refresh_schedule()
        today = self._schedule_cache.get("trains_today", ([], False))[0]
        tomorrow = self._schedule_cache.get("trains_tomorrow", ([], False))[0]
        next_train = _next_train_from_lists(today, tomorrow)
        return format_next_train_report(next_train)

    async def next_tgv_report(self) -> str:
        if not self._cache_has_today():
            await self.refresh_schedule()
        today = self._schedule_cache.get("trains_today", ([], False))[0]
        tomorrow = self._schedule_cache.get("trains_tomorrow", ([], False))[0]
        tgv = _next_tgv_from_lists(today, tomorrow)
        msg = format_next_tgv(tgv)
        return msg.strip()


def _unpack(result: object, label: str) -> tuple[list[Arrival], bool]:
    if isinstance(result, list):
        return result, True
    logger.error("%s raised: %s", label, result)
    return [], False
