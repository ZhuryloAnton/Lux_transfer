"""Train arrivals at Gare Centrale Luxembourg from official GTFS timetable.

Data source: Luxembourg government open data (data.public.lu)
Only arrivals at Gare Centrale Luxembourg are returned — no departures,
no other stations, no transit-only rows.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
import zipfile
from datetime import datetime, timedelta
from typing import Any

import pytz

from bot.models import Arrival, TransportType
from utils.http import fetch_bytes, fetch_json

logger = logging.getLogger(__name__)

LUX_TZ = pytz.timezone("Europe/Luxembourg")

_GTFS_DATASET_URL = (
    "https://data.public.lu/api/1/datasets/"
    "horaires-et-arrets-des-transport-publics-gtfs/"
)

# Official stop_id for Gare Centrale Luxembourg in the CFL GTFS feed.
# This is verified against the published stops.txt file.
_GARE_CENTRALE_STOP_ID = "000200405060"

# Only include real train service types — skip bus/tram route names
_TRAIN_ROUTE_TYPES = frozenset({"ICE", "TGV", "IC", "EC", "RE", "RB", "TER", "IR"})

# GTFS cache: holds the downloaded ZipFile + a timestamp for staleness checks
_GTFS_MAX_AGE_SECONDS = 6 * 3_600  # refresh every 6 hours


class TrainDataSource:
    """Real train arrivals at Gare Centrale from Luxembourg GTFS data.

    The GTFS zip is downloaded once and refreshed every 6 hours so the
    in-memory copy stays current without hammering the open-data portal.
    Never returns mock or fallback data.
    """

    def __init__(self) -> None:
        self._gtfs_zip: zipfile.ZipFile | None = None
        self._gtfs_fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def fetch_today(self) -> list[Arrival]:
        """Future arrivals at Gare Centrale for the rest of today."""
        now = datetime.now(tz=LUX_TZ)
        try:
            raw = await self._fetch_for_date(now)
        except Exception as exc:
            logger.error("GTFS fetch failed (today): %s", exc)
            return []
        return self._filter_future(raw, after=now)

    async def fetch_tomorrow(self) -> list[Arrival]:
        """All arrivals at Gare Centrale for the whole of tomorrow."""
        now = datetime.now(tz=LUX_TZ)
        tomorrow = now + timedelta(days=1)
        try:
            raw = await self._fetch_for_date(tomorrow)
        except Exception as exc:
            logger.error("GTFS fetch failed (tomorrow): %s", exc)
            return []
        start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        return self._filter_future(raw, after=start)

    async def get_next_tgv(self) -> Arrival | None:
        """Return the next TGV arrival at Gare Centrale (today or tomorrow)."""
        now = datetime.now(tz=LUX_TZ)
        for day_offset in (0, 1):
            target = now + timedelta(days=day_offset)
            try:
                raw = await self._fetch_for_date(target)
            except Exception:
                continue
            tgvs = [
                a for a in self._filter_future(raw, after=now)
                if a.identifier == "TGV"
            ]
            if tgvs:
                return min(tgvs, key=lambda a: a.effective_time)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_gtfs(self) -> zipfile.ZipFile:
        """Return a cached GTFS ZipFile, refreshing if stale."""
        async with self._lock:
            age = time.monotonic() - self._gtfs_fetched_at
            if self._gtfs_zip is None or age > _GTFS_MAX_AGE_SECONDS:
                logger.info("Downloading GTFS zip (age=%.0fs)…", age)
                self._gtfs_zip = await self._download_gtfs()
                self._gtfs_fetched_at = time.monotonic()
                logger.info("GTFS zip downloaded and cached")
            return self._gtfs_zip

    async def _download_gtfs(self) -> zipfile.ZipFile:
        """Resolve the GTFS zip URL via the dataset API, then download it."""
        dataset = await fetch_json(_GTFS_DATASET_URL, ssl=False)
        if not isinstance(dataset, dict):
            raise ValueError("GTFS dataset API returned unexpected format")

        resources = dataset.get("resources", [])
        zip_url: str | None = None
        for r in resources:
            if r.get("format", "").lower() == "zip" and r.get("url"):
                zip_url = r["url"]
                break
        if not zip_url:
            raise ValueError("No GTFS zip resource found in dataset API response")

        content = await fetch_bytes(zip_url, ssl=False)
        return zipfile.ZipFile(io.BytesIO(content))

    async def _fetch_for_date(self, target: datetime) -> list[Arrival]:
        """Parse GTFS for a specific date and return Arrivals at Gare Centrale."""
        zf = await self._get_gtfs()
        target_str = target.strftime("%Y%m%d")
        weekday = target.strftime("%A").lower()

        active_services = self._active_services(zf, target_str, weekday)
        if not active_services:
            logger.warning("No active GTFS services for %s", target_str)
            return []

        route_map = self._read_routes(zf)
        active_trips = self._active_trips(zf, active_services, route_map)
        return self._stop_times(zf, active_trips, target_str)

    @staticmethod
    def _active_services(
        zf: zipfile.ZipFile, target_str: str, weekday: str
    ) -> set[str]:
        services: set[str] = set()
        with zf.open("calendar.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                start = row.get("start_date", "")
                end = row.get("end_date", "")
                if start <= target_str <= end and row.get(weekday, "0") == "1":
                    services.add(row["service_id"])
        return services

    @staticmethod
    def _read_routes(zf: zipfile.ZipFile) -> dict[str, str]:
        """Map route_id → route_short_name."""
        route_map: dict[str, str] = {}
        with zf.open("routes.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                route_map[row["route_id"]] = row.get("route_short_name", "")
        return route_map

    @staticmethod
    def _active_trips(
        zf: zipfile.ZipFile,
        active_services: set[str],
        route_map: dict[str, str],
    ) -> dict[str, dict]:
        """Map trip_id → trip row, only for active services with train route types."""
        trips: dict[str, dict] = {}
        with zf.open("trips.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                if row.get("service_id") not in active_services:
                    continue
                route_name = route_map.get(row.get("route_id", ""), "")
                if route_name not in _TRAIN_ROUTE_TYPES:
                    continue
                row["_route_name"] = route_name
                trips[row["trip_id"]] = row
        return trips

    @staticmethod
    def _stop_times(
        zf: zipfile.ZipFile,
        active_trips: dict[str, dict],
        date_str: str,
    ) -> list[Arrival]:
        """Extract arrival rows at Gare Centrale and build Arrival objects."""
        arrivals: list[Arrival] = []
        with zf.open("stop_times.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                if row.get("stop_id") != _GARE_CENTRALE_STOP_ID:
                    continue
                trip = active_trips.get(row.get("trip_id", ""))
                if trip is None:
                    continue
                arr_str = row.get("arrival_time", "")
                if not arr_str:
                    continue

                arrival = TrainDataSource._build_arrival(arr_str, date_str, trip)
                if arrival is not None:
                    arrivals.append(arrival)

        return sorted(arrivals, key=lambda a: a.effective_time)

    @staticmethod
    def _build_arrival(
        time_str: str, date_str: str, trip: dict
    ) -> Arrival | None:
        parts = time_str.split(":")
        if len(parts) < 2:
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError:
            return None

        # GTFS allows hour >= 24 for post-midnight times on the same service day
        day_offset = 0
        if hour >= 24:
            hour -= 24
            day_offset = 1

        try:
            base = datetime.strptime(date_str, "%Y%m%d")
            dt = LUX_TZ.localize(base.replace(hour=hour, minute=minute, second=0))
            if day_offset:
                dt += timedelta(days=1)
        except (ValueError, TypeError):
            return None

        route_name = trip.get("_route_name", "")
        headsign = trip.get("trip_headsign", "")
        origin = (
            headsign.replace(", Gare", "").replace(" Gare", "").strip()
            if headsign
            else "Unknown"
        )

        return Arrival(
            transport_type=TransportType.TRAIN,
            scheduled_time=dt,
            identifier=route_name,
            origin=origin,
            status="scheduled",
        )

    @staticmethod
    def _filter_future(arrivals: list[Arrival], *, after: datetime) -> list[Arrival]:
        return [a for a in arrivals if a.effective_time >= after]
