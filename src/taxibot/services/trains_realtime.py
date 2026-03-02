"""Real-time train delays from GTFS-RT (e.g. OpenOV Luxembourg).

Fetches trip updates every N minutes and exposes delay per (trip_id, stop_id)
so the static GTFS schedule can be merged with live delays.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from taxibot.core.http import fetch_bytes

logger = logging.getLogger(__name__)

_DEFAULT_GTFS_RT_URL = "http://openov.lu/gtfs-rt/tripUpdates.pb"
_CACHE_TTL_SECONDS = 600  # 10 minutes


def _parse_trip_updates(raw: bytes) -> dict[tuple[str, str], int]:
    """Parse GTFS-RT FeedMessage (trip updates) -> (trip_id, stop_id) -> delay_seconds."""
    out: dict[tuple[str, str], int] = {}
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError:
        logger.warning("gtfs-realtime-bindings not installed; real-time delays disabled")
        return out
    try:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(raw)
    except Exception as e:
        logger.warning("GTFS-RT parse failed: %s", e)
        return out
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id if tu.trip else ""
        if not trip_id:
            continue
        for stu in tu.stop_time_update:
            stop_id = stu.stop_id if stu.stop_id else ""
            if not stop_id:
                continue
            delay_sec = 0
            if stu.HasField("arrival") and stu.arrival.HasField("delay"):
                delay_sec = stu.arrival.delay
            elif stu.HasField("departure") and stu.departure.HasField("delay"):
                delay_sec = stu.departure.delay
            if delay_sec > 0:
                out[(trip_id, stop_id)] = delay_sec
    return out


class RealtimeDelayCache:
    """Cache of (trip_id, stop_id) -> delay_seconds from GTFS-RT. Refresh periodically."""

    def __init__(
        self,
        gtfs_rt_url: str = "",
        cache_ttl_seconds: int = _CACHE_TTL_SECONDS,
    ) -> None:
        self._url = (gtfs_rt_url or _DEFAULT_GTFS_RT_URL).strip()
        self._ttl = cache_ttl_seconds
        self._delay_map: dict[tuple[str, str], int] = {}
        self._last_fetch: float = 0
        self._lock = asyncio.Lock()

    def get_delay_seconds(self, trip_id: str, stop_id: str) -> int | None:
        """Return delay in seconds for (trip_id, stop_id), or None if unknown/not delayed."""
        if not trip_id or not stop_id:
            return None
        return self._delay_map.get((trip_id, stop_id))

    def get_delay_minutes(self, trip_id: str, stop_id: str) -> int | None:
        """Return delay in minutes (>= 0), or None."""
        s = self.get_delay_seconds(trip_id, stop_id)
        if s is None or s <= 0:
            return None
        return s // 60

    async def refresh(self) -> None:
        """Fetch GTFS-RT feed and update the delay map. Call every 10 min (e.g. from job queue)."""
        async with self._lock:
            try:
                raw = await fetch_bytes(self._url)
            except Exception as e:
                logger.warning("GTFS-RT fetch failed: %s", e)
                return
            self._delay_map = _parse_trip_updates(raw)
            self._last_fetch = time.monotonic()
            logger.info("GTFS-RT refreshed: %d delay entries", len(self._delay_map))

    def is_stale(self) -> bool:
        """True if cache is older than TTL (should refresh)."""
        return (time.monotonic() - self._last_fetch) >= self._ttl

    async def ensure_fresh(self) -> None:
        """Refresh if cache is stale (e.g. call at start of each report)."""
        if self.is_stale():
            await self.refresh()
