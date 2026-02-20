"""Train arrivals at Gare Centrale Luxembourg.

Two data sources:
  1. Real-time: api.tfl.lu StopPoint Departures API  — used for today/next 3h
  2. Schedule:  Luxembourg GTFS (data.public.lu)      — used for tomorrow

Stop ID for Luxembourg, Gare Centrale: 200405035
(verified via api.tfl.lu/v1/StopPoint/around/6.133646/49.60067/100)

Only TRAIN type services are returned. Bus/tram rows are filtered out.
Only arrivals where Gare Centrale is the terminal destination are counted —
transit trains that pass through are excluded from the real-time feed by
filtering on destination. The GTFS feed is filtered by stop_id and
arrival_time (not departure_time) to guarantee arrivals only.
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

# Verified stop ID for "Luxembourg, Gare Centrale" in the TfL Luxembourg API.
# Source: GET api.tfl.lu/v1/StopPoint/around/6.133646/49.60067/100
_STOP_ID = 200405035

# Real-time departures endpoint (TfL Luxembourg open API, CC0 licensed)
_TFL_DEPARTURES_URL = f"https://api.tfl.lu/v1/StopPoint/Departures/{_STOP_ID}"

# GTFS static timetable — Luxembourg government open data
_GTFS_DATASET_URL = (
    "https://data.public.lu/api/1/datasets/"
    "horaires-et-arrets-des-transport-publics-gtfs/"
)

# stop_id as it appears in the GTFS stops.txt file for Gare Centrale.
# Format in Luxembourg GTFS omits leading zeros compared to the TfL API integer.
_GTFS_STOP_ID = "200405035"

# Train route_short_name values — used to exclude bus/tram rows from GTFS
_TRAIN_TYPES = frozenset({"ICE", "TGV", "IC", "EC", "RE", "RB", "TER", "IR", "CRE", "CRN"})

# Gare Centrale is a terminal for most trains; destination strings that confirm
# this train ends at Gare Centrale (used to filter real-time feed).
_TERMINAL_KEYWORDS = (
    "luxembourg, gare centrale",
    "luxembourg gare centrale",
    "luxembourg centrale",
)

# GTFS zip cache: refresh every 6 hours
_GTFS_MAX_AGE_SECONDS = 6 * 3_600


# ------------------------------------------------------------------
# Public interface
# ------------------------------------------------------------------


class TrainDataSource:
    """Train arrivals at Gare Centrale Luxembourg.

    - fetch_today()    → real-time TfL API, future arrivals within next 3h window
    - fetch_tomorrow() → GTFS static schedule, full day tomorrow
    - get_next_tgv()   → first TGV arrival (today or tomorrow)
    """

    def __init__(self) -> None:
        self._gtfs_zip: zipfile.ZipFile | None = None
        self._gtfs_fetched_at: float = 0.0
        self._gtfs_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def fetch_today(self) -> list[Arrival]:
        """Real-time train arrivals at Gare Centrale for the rest of today."""
        now = datetime.now(tz=LUX_TZ)
        try:
            raw = await fetch_json(_TFL_DEPARTURES_URL, ssl=False)
        except Exception as exc:
            logger.error("TfL departures API failed: %s", exc)
            # Fall back to GTFS for today if the real-time API is down
            return await self._gtfs_fetch_today_fallback(now)

        arrivals = self._parse_tfl_response(raw, now)
        logger.info("TfL API: %d train arrivals at Gare Centrale", len(arrivals))
        return arrivals

    async def fetch_tomorrow(self) -> list[Arrival]:
        """GTFS scheduled train arrivals at Gare Centrale for all of tomorrow."""
        now = datetime.now(tz=LUX_TZ)
        tomorrow = now + timedelta(days=1)
        tomorrow_start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            arrivals = await self._gtfs_for_date(tomorrow)
        except Exception as exc:
            logger.error("GTFS fetch failed (tomorrow): %s", exc)
            return []
        result = [a for a in arrivals if a.effective_time >= tomorrow_start]
        logger.info("GTFS: %d train arrivals for tomorrow at Gare Centrale", len(result))
        return result

    async def get_next_tgv(self) -> Arrival | None:
        """Return the next TGV arriving at Gare Centrale (today or tomorrow)."""
        now = datetime.now(tz=LUX_TZ)
        for day_offset in (0, 1):
            target = now + timedelta(days=day_offset)
            try:
                arrivals = await self._gtfs_for_date(target)
                if day_offset == 0:
                    arrivals = [a for a in arrivals if a.effective_time > now]
            except Exception:
                continue
            tgvs = [a for a in arrivals if a.identifier.upper() == "TGV"]
            if tgvs:
                return min(tgvs, key=lambda a: a.effective_time)
        return None

    # ------------------------------------------------------------------
    # Real-time TfL API parsing
    # ------------------------------------------------------------------

    def _parse_tfl_response(
        self, raw: object, now: datetime
    ) -> list[Arrival]:
        """Parse the TfL StopPoint/Departures response.

        The API returns a list of departure objects for the stop.
        We keep only:
          - type == "train"
          - destination is Gare Centrale (terminal trains only)
          - departureISO is in the future
        Delay is in seconds in the API response.
        """
        if not isinstance(raw, list):
            logger.warning("TfL API response is not a list: %s", type(raw))
            return []

        arrivals: list[Arrival] = []
        for entry in raw:
            a = self._parse_tfl_entry(entry, now)
            if a is not None:
                arrivals.append(a)

        return sorted(arrivals, key=lambda a: a.effective_time)

    @staticmethod
    def _parse_tfl_entry(entry: dict, now: datetime) -> Arrival | None:
        if not isinstance(entry, dict):
            return None

        # Only train services
        if entry.get("type") != "train":
            return None

        # Only trains terminating at Gare Centrale
        destination = (entry.get("destination") or "").lower().strip()
        if not any(kw in destination for kw in _TERMINAL_KEYWORDS):
            return None

        # Parse scheduled departure time
        dep_iso = entry.get("departureISO") or ""
        if not dep_iso:
            return None
        try:
            scheduled = datetime.fromisoformat(dep_iso)
            if scheduled.tzinfo is None:
                scheduled = LUX_TZ.localize(scheduled)
        except (ValueError, TypeError):
            return None

        # Delay in seconds
        delay_secs = entry.get("delay") or 0
        try:
            delay_minutes = max(0, int(delay_secs) // 60)
        except (ValueError, TypeError):
            delay_minutes = 0

        effective = scheduled + timedelta(minutes=delay_minutes)
        if effective < now:
            return None

        # Line name as identifier (e.g. "RE", "IC", "TGV")
        line = (entry.get("line") or "").strip()
        train_id = entry.get("trainId")
        identifier = f"{line} {train_id}".strip() if train_id else line or "Train"

        # The real-time departures feed gives destination, not origin.
        origin = "—"

        return Arrival(
            transport_type=TransportType.TRAIN,
            scheduled_time=scheduled,
            identifier=identifier,
            origin=origin,
            status="live" if entry.get("live") else "scheduled",
            delay_minutes=delay_minutes,
        )

    # ------------------------------------------------------------------
    # GTFS helpers
    # ------------------------------------------------------------------

    async def _gtfs_fetch_today_fallback(self, now: datetime) -> list[Arrival]:
        """GTFS fallback when the real-time API is unavailable."""
        try:
            arrivals = await self._gtfs_for_date(now)
            return [a for a in arrivals if a.effective_time >= now]
        except Exception as exc:
            logger.error("GTFS fallback also failed: %s", exc)
            return []

    async def _gtfs_for_date(self, target: datetime) -> list[Arrival]:
        """Return all GTFS train arrivals at Gare Centrale for the target date."""
        zf = await self._get_gtfs()
        target_str = target.strftime("%Y%m%d")
        weekday = target.strftime("%A").lower()

        active_services = self._active_services(zf, target_str, weekday)
        if not active_services:
            logger.warning("No active GTFS services for %s", target_str)
            return []

        route_map = self._read_routes(zf)
        active_trips = self._active_trips(zf, active_services, route_map)
        if not active_trips:
            logger.warning("No active train trips in GTFS for %s", target_str)
            return []

        return self._extract_arrivals(zf, active_trips, target_str)

    async def _get_gtfs(self) -> zipfile.ZipFile:
        """Return a cached GTFS ZipFile, re-downloading if older than 6 hours."""
        async with self._gtfs_lock:
            age = time.monotonic() - self._gtfs_fetched_at
            if self._gtfs_zip is None or age > _GTFS_MAX_AGE_SECONDS:
                logger.info("Downloading GTFS zip (cache age=%.0fs)…", age)
                self._gtfs_zip = await self._download_gtfs()
                self._gtfs_fetched_at = time.monotonic()
                logger.info("GTFS zip cached successfully")
            return self._gtfs_zip

    async def _download_gtfs(self) -> zipfile.ZipFile:
        dataset = await fetch_json(_GTFS_DATASET_URL, ssl=False)
        if not isinstance(dataset, dict):
            raise ValueError("GTFS dataset API returned unexpected format")
        zip_url: str | None = None
        for r in dataset.get("resources", []):
            if r.get("format", "").lower() == "zip" and r.get("url"):
                zip_url = r["url"]
                break
        if not zip_url:
            raise ValueError("No GTFS zip URL found in dataset resources")
        content = await fetch_bytes(zip_url, ssl=False)
        return zipfile.ZipFile(io.BytesIO(content))

    @staticmethod
    def _active_services(
        zf: zipfile.ZipFile, target_str: str, weekday: str
    ) -> set[str]:
        """Determine active service_ids for the target date.

        Combines calendar.txt (regular weekly pattern) with
        calendar_dates.txt (explicit additions/removals for holidays etc.).
        """
        services: set[str] = set()

        # Regular weekly schedule
        if "calendar.txt" in zf.namelist():
            with zf.open("calendar.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                for row in reader:
                    start = row.get("start_date", "")
                    end = row.get("end_date", "")
                    if start <= target_str <= end and row.get(weekday, "0") == "1":
                        services.add(row["service_id"])

        # Exception dates (1 = added, 2 = removed)
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
        """Map trip_id → enriched trip dict for active train services only."""
        trips: dict[str, dict] = {}
        with zf.open("trips.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                if row.get("service_id") not in active_services:
                    continue
                route_name = route_map.get(row.get("route_id", ""), "").strip()
                if route_name.upper() not in _TRAIN_TYPES:
                    continue
                row["_route_name"] = route_name.upper()
                trips[row["trip_id"]] = row
        return trips

    @staticmethod
    def _extract_arrivals(
        zf: zipfile.ZipFile,
        active_trips: dict[str, dict],
        date_str: str,
    ) -> list[Arrival]:
        """Scan stop_times.txt for Gare Centrale rows and build Arrival objects.

        Uses arrival_time (not departure_time) to capture the moment the
        train reaches the station, not when it leaves.
        Only rows where stop_id == _GTFS_STOP_ID are included.
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
                arrival = TrainDataSource._build_gtfs_arrival(arr_str, date_str, trip)
                if arrival is not None:
                    arrivals.append(arrival)

        return sorted(arrivals, key=lambda a: a.effective_time)

    @staticmethod
    def _build_gtfs_arrival(
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

        # GTFS allows hour >= 24 for post-midnight service continuations
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

        # Clean up origin from headsign — remove Gare Centrale references
        if headsign:
            origin = (
                headsign
                .replace("Luxembourg, Gare Centrale", "")
                .replace(", Gare Centrale", "")
                .replace("Gare Centrale", "")
                .replace(" - ", "")
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