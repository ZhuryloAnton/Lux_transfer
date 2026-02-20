"""Train arrivals at Gare Centrale Luxembourg from official GTFS timetable.

Data source: Luxembourg government open data (data.public.lu)
  GET https://data.public.lu/api/1/datasets/horaires-et-arrets-des-transport-publics-gtfs/

Why GTFS only (no real-time API):
  The TfL Luxembourg API (api.tfl.lu) only exposes a Departures endpoint
  (/StopPoint/Departures/{id}), which returns trains LEAVING a stop.
  The Arrivals endpoint (/StopPoint/Arrivals/{id}) is explicitly marked
  UNAVAILABLE in the official TfL Luxembourg API documentation.
  GTFS covers all scheduled arrivals reliably for both today and tomorrow.

Stop ID for Luxembourg, Gare Centrale in GTFS stops.txt: "200405035"
  Verified via api.tfl.lu/v1/StopPoint/around/6.133646/49.60067/100
  which returns id=200405035, name="Luxembourg, Gare Centrale".

Only real train services are returned (TRAIN_TYPES filter).
stop_times.txt is filtered by arrival_time, not departure_time.
calendar_dates.txt exceptions (holidays) are applied on top of calendar.txt.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
import zipfile
from datetime import datetime, timedelta

import pytz

from bot.models import Arrival, TransportType
from utils.http import fetch_bytes, fetch_json

logger = logging.getLogger(__name__)

LUX_TZ = pytz.timezone("Europe/Luxembourg")

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

# GTFS static timetable — Luxembourg government open data portal
_GTFS_DATASET_URL = (
    "https://data.public.lu/api/1/datasets/"
    "horaires-et-arrets-des-transport-publics-gtfs/"
)

# stop_id for "Luxembourg, Gare Centrale" as it appears in GTFS stops.txt.
# Verified against api.tfl.lu/v1/StopPoint/around/6.133646/49.60067/100
# which returns id=200405035, name="Luxembourg, Gare Centrale".
_GTFS_STOP_ID = "200405035"

# route_short_name values that represent real train services in Luxembourg GTFS.
# Rows with other route names (bus, tram) are excluded.
_TRAIN_TYPES = frozenset({"ICE", "TGV", "IC", "EC", "RE", "RB", "TER", "IR", "CRE", "CRN"})

# GTFS zip cache TTL — refresh every 6 hours so schedules stay current
_GTFS_MAX_AGE_SECONDS = 6 * 3_600


# ------------------------------------------------------------------
# Public class
# ------------------------------------------------------------------


class TrainDataSource:
    """Scheduled train arrivals at Gare Centrale from Luxembourg GTFS.

    Public interface:
      fetch_today()    → arrivals from now until end of today
      fetch_tomorrow() → all arrivals for tomorrow (full day)
      get_next_tgv()   → next TGV arrival (today or tomorrow)
    """

    def __init__(self) -> None:
        self._gtfs_zip: zipfile.ZipFile | None = None
        self._gtfs_fetched_at: float = 0.0
        self._gtfs_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def fetch_today(self) -> list[Arrival]:
        """Return future train arrivals at Gare Centrale for the rest of today."""
        now = datetime.now(tz=LUX_TZ)
        try:
            arrivals = await self._gtfs_for_date(now)
        except Exception as exc:
            logger.error("GTFS fetch failed (today): %s", exc)
            return []
        result = [a for a in arrivals if a.effective_time >= now]
        logger.info("GTFS: %d future train arrivals today at Gare Centrale", len(result))
        return result

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Return all train arrivals at Gare Centrale for tomorrow."""
        now = datetime.now(tz=LUX_TZ)
        tomorrow = now + timedelta(days=1)
        tomorrow_start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            arrivals = await self._gtfs_for_date(tomorrow)
        except Exception as exc:
            logger.error("GTFS fetch failed (tomorrow): %s", exc)
            return []
        result = [a for a in arrivals if a.effective_time >= tomorrow_start]
        logger.info("GTFS: %d train arrivals tomorrow at Gare Centrale", len(result))
        return result

    async def get_next_tgv(self) -> Arrival | None:
        """Return the next TGV arriving at Gare Centrale (today or tomorrow)."""
        now = datetime.now(tz=LUX_TZ)
        for day_offset in (0, 1):
            target = now + timedelta(days=day_offset)
            try:
                arrivals = await self._gtfs_for_date(target)
            except Exception:
                continue
            tgvs = [
                a for a in arrivals
                if a.identifier.upper() == "TGV" and a.effective_time > now
            ]
            if tgvs:
                return min(tgvs, key=lambda a: a.effective_time)
        return None

    # ------------------------------------------------------------------
    # GTFS download & caching
    # ------------------------------------------------------------------

    async def _get_gtfs(self) -> zipfile.ZipFile:
        """Return a cached GTFS ZipFile, re-downloading if older than 6 hours."""
        async with self._gtfs_lock:
            age = time.monotonic() - self._gtfs_fetched_at
            if self._gtfs_zip is None or age > _GTFS_MAX_AGE_SECONDS:
                logger.info("Downloading GTFS zip (cache age=%.0fs)…", age)
                self._gtfs_zip = await self._download_gtfs()
                self._gtfs_fetched_at = time.monotonic()
                logger.info("GTFS zip downloaded and cached")
            return self._gtfs_zip

    async def _download_gtfs(self) -> zipfile.ZipFile:
        """Resolve the GTFS zip URL via the open-data API, then download it."""
        dataset = await fetch_json(_GTFS_DATASET_URL, ssl=False)
        if not isinstance(dataset, dict):
            raise ValueError("GTFS dataset API returned unexpected format")
        zip_url: str | None = None
        for resource in dataset.get("resources", []):
            if resource.get("format", "").lower() == "zip" and resource.get("url"):
                zip_url = resource["url"]
                break
        if not zip_url:
            raise ValueError("No GTFS zip URL found in dataset API response")
        content = await fetch_bytes(zip_url, ssl=False)
        return zipfile.ZipFile(io.BytesIO(content))

    # ------------------------------------------------------------------
    # GTFS parsing pipeline
    # ------------------------------------------------------------------

    async def _gtfs_for_date(self, target: datetime) -> list[Arrival]:
        """Full pipeline: download → parse → filter for a specific date."""
        zf = await self._get_gtfs()
        target_str = target.strftime("%Y%m%d")
        weekday = target.strftime("%A").lower()

        active_services = self._active_services(zf, target_str, weekday)
        if not active_services:
            logger.warning("No active GTFS services found for %s", target_str)
            return []

        route_map = self._read_routes(zf)
        active_trips = self._active_trips(zf, active_services, route_map)
        if not active_trips:
            logger.warning("No active train trips in GTFS for %s", target_str)
            return []

        arrivals = self._extract_arrivals(zf, active_trips, target_str)
        logger.debug("GTFS parsed %d arrivals at Gare Centrale for %s", len(arrivals), target_str)
        return arrivals

    @staticmethod
    def _active_services(
        zf: zipfile.ZipFile, target_str: str, weekday: str
    ) -> set[str]:
        """Return service_ids active on target_str.

        Reads calendar.txt for the regular weekly pattern, then applies
        calendar_dates.txt overrides (type 1 = added, type 2 = removed)
        so public holidays and special schedules are handled correctly.
        """
        services: set[str] = set()

        if "calendar.txt" in zf.namelist():
            with zf.open("calendar.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                for row in reader:
                    start = row.get("start_date", "")
                    end = row.get("end_date", "")
                    if start <= target_str <= end and row.get(weekday, "0") == "1":
                        services.add(row["service_id"])

        if "calendar_dates.txt" in zf.namelist():
            with zf.open("calendar_dates.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                for row in reader:
                    if row.get("date") != target_str:
                        continue
                    sid = row.get("service_id", "")
                    exc_type = row.get("exception_type", "")
                    if exc_type == "1":
                        services.add(sid)
                    elif exc_type == "2":
                        services.discard(sid)

        return services

    @staticmethod
    def _read_routes(zf: zipfile.ZipFile) -> dict[str, str]:
        """Build a route_id → route_short_name mapping."""
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
        """Build a trip_id → trip dict for active services with train route types."""
        trips: dict[str, dict] = {}
        with zf.open("trips.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                if row.get("service_id") not in active_services:
                    continue
                route_name = route_map.get(row.get("route_id", ""), "").strip().upper()
                if route_name not in _TRAIN_TYPES:
                    continue
                row["_route_name"] = route_name
                trips[row["trip_id"]] = row
        return trips

    @staticmethod
    def _extract_arrivals(
        zf: zipfile.ZipFile,
        active_trips: dict[str, dict],
        date_str: str,
    ) -> list[Arrival]:
        """Scan stop_times.txt for rows at Gare Centrale and build Arrival objects.

        Filters on stop_id == _GTFS_STOP_ID.
        Uses arrival_time (not departure_time) — the moment the train
        reaches Gare Centrale, not when it leaves.
        """
        arrivals: list[Arrival] = []
        with zf.open("stop_times.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                if row.get("stop_id") != _GTFS_STOP_ID:
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
    def _build_arrival(time_str: str, date_str: str, trip: dict) -> Arrival | None:
        """Parse a GTFS time string and trip dict into an Arrival object."""
        parts = time_str.split(":")
        if len(parts) < 2:
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError:
            return None

        # GTFS allows hour >= 24 for services running past midnight on the same
        # service day (e.g. "25:30" = 01:30 the following calendar day).
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

        route_name = trip.get("_route_name", "Train")
        headsign = (trip.get("trip_headsign") or "").strip()

        # Derive origin from headsign. For trains arriving at Gare Centrale,
        # the headsign is the terminus of the trip which is Gare Centrale itself.
        # We extract the meaningful origin portion by stripping known suffixes.
        if headsign:
            origin = (
                headsign
                .replace("Luxembourg, Gare Centrale", "")
                .replace(", Gare Centrale", "")
                .replace("Gare Centrale", "")
                .strip(" -,")
                or headsign
            )
        else:
            origin = "—"

        return Arrival(
            transport_type=TransportType.TRAIN,
            scheduled_time=dt,
            identifier=route_name,
            origin=origin,
            status="scheduled",
        )
