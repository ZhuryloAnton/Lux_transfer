"""Train arrivals at Gare Centrale Luxembourg — Luxembourg GTFS timetable.

Data source
-----------
Luxembourg Government open data portal (CC0):
  https://data.public.lu/api/1/datasets/horaires-et-arrets-des-transport-publics-gtfs/

Why GTFS only (no real-time API)
---------------------------------
The TfL Luxembourg API (api.tfl.lu) only exposes a *Departures* endpoint
  GET /v1/StopPoint/Departures/{stopId}
which returns trains **leaving** a stop.  The Arrivals endpoint
  GET /v1/StopPoint/Arrivals/{stopId}
is explicitly marked **UNAVAILABLE** in the official TfL Luxembourg API docs.

GTFS stop_id for "Luxembourg, Gare Centrale": "200405035"
  Verified via api.tfl.lu/v1/StopPoint/around/6.133646/49.60067/100

GTFS caching
------------
The zip (~5 MB) is downloaded once and cached in-process for 6 hours.
An asyncio.Lock prevents concurrent re-downloads.

Holiday handling
----------------
calendar.txt  — regular weekly pattern (Mon–Sun flags)
calendar_dates.txt — exception dates (type 1 = added, type 2 = removed)
Both are applied so public holidays are handled correctly.
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

_LUX_TZ = pytz.timezone("Europe/Luxembourg")

# ── Constants ─────────────────────────────────────────────────────────────────

_GTFS_DATASET_URL = (
    "https://data.public.lu/api/1/datasets/"
    "horaires-et-arrets-des-transport-publics-gtfs/"
)

# stop_id in GTFS stops.txt for "Luxembourg, Gare Centrale"
_STOP_ID = "200405035"

# route_short_name values that identify real train services
# (excludes bus, tram, and other non-rail modes)
_TRAIN_TYPES = frozenset({
    "ICE", "TGV", "IC", "EC", "RE", "RB", "TER", "IR", "CRE", "CRN",
})

# Re-download the GTFS zip if older than 6 hours
_GTFS_TTL = 6 * 3_600


# ── Data source ───────────────────────────────────────────────────────────────

class TrainDataSource:
    """Scheduled train arrivals at Gare Centrale from the Luxembourg GTFS feed."""

    def __init__(self) -> None:
        self._zip: zipfile.ZipFile | None = None
        self._zip_fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    # ── Public interface ──────────────────────────────────────────────────────

    async def fetch_today(self) -> list[Arrival]:
        """Return train arrivals at Gare Centrale from now until end of today."""
        now = datetime.now(tz=_LUX_TZ)
        try:
            all_arrivals = await self._for_date(now)
        except Exception as exc:
            logger.error("GTFS fetch failed (today): %s", exc)
            return []
        result = [a for a in all_arrivals if a.effective_time >= now]
        logger.info("GTFS today: %d future arrivals at Gare Centrale.", len(result))
        return result

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Return all train arrivals at Gare Centrale for tomorrow."""
        now      = datetime.now(tz=_LUX_TZ)
        tomorrow = now + timedelta(days=1)
        start    = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            all_arrivals = await self._for_date(tomorrow)
        except Exception as exc:
            logger.error("GTFS fetch failed (tomorrow): %s", exc)
            return []
        result = [a for a in all_arrivals if a.effective_time >= start]
        logger.info("GTFS tomorrow: %d arrivals at Gare Centrale.", len(result))
        return result

    async def get_next_tgv(self) -> Arrival | None:
        """Return the next TGV arriving at Gare Centrale (today or tomorrow)."""
        now = datetime.now(tz=_LUX_TZ)
        for offset in (0, 1):
            target = now + timedelta(days=offset)
            try:
                arrivals = await self._for_date(target)
            except Exception:
                continue
            tgvs = [
                a for a in arrivals
                if a.identifier == "TGV" and a.effective_time > now
            ]
            if tgvs:
                return min(tgvs, key=lambda a: a.effective_time)
        return None

    # ── GTFS download & cache ─────────────────────────────────────────────────

    async def _get_zip(self) -> zipfile.ZipFile:
        """Return cached GTFS zip, re-downloading when the TTL has expired."""
        async with self._lock:
            age = time.monotonic() - self._zip_fetched_at
            if self._zip is None or age > _GTFS_TTL:
                logger.info("Downloading GTFS zip (cache age %.0fs)…", age)
                self._zip = await self._download_zip()
                self._zip_fetched_at = time.monotonic()
                logger.info("GTFS zip cached.")
            return self._zip

    async def _download_zip(self) -> zipfile.ZipFile:
        dataset = await fetch_json(_GTFS_DATASET_URL, ssl=False)
        if not isinstance(dataset, dict):
            raise ValueError("GTFS dataset API: unexpected response type")

        zip_url: str | None = None
        for resource in dataset.get("resources", []):
            fmt = (resource.get("format") or "").lower()
            url = resource.get("url") or ""
            if fmt == "zip" and url:
                zip_url = url
                break

        if not zip_url:
            raise ValueError("GTFS dataset API: no zip URL found in resources")

        content = await fetch_bytes(zip_url, ssl=False)
        return zipfile.ZipFile(io.BytesIO(content))

    # ── GTFS parsing pipeline ─────────────────────────────────────────────────

    async def _for_date(self, target: datetime) -> list[Arrival]:
        zf         = await self._get_zip()
        target_str = target.strftime("%Y%m%d")
        weekday    = target.strftime("%A").lower()

        services = _active_services(zf, target_str, weekday)
        if not services:
            logger.warning("GTFS: no active services for %s.", target_str)
            return []

        route_map = _read_routes(zf)
        trips     = _active_trips(zf, services, route_map)
        if not trips:
            logger.warning("GTFS: no active train trips for %s.", target_str)
            return []

        arrivals = _extract_arrivals(zf, trips, target_str)
        logger.debug("GTFS: %d arrivals at Gare Centrale for %s.", len(arrivals), target_str)
        return arrivals


# ── Pure GTFS parsing functions (no I/O, no state) ───────────────────────────

def _active_services(
    zf: zipfile.ZipFile, target_str: str, weekday: str
) -> set[str]:
    """Return service_ids running on *target_str*.

    Step 1: calendar.txt  — services whose date range includes today and whose
            weekday flag is set.
    Step 2: calendar_dates.txt — exception overrides applied on top:
            exception_type=1 → add service, exception_type=2 → remove service.
    """
    services: set[str] = set()

    names = zf.namelist()

    if "calendar.txt" in names:
        with zf.open("calendar.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                start = row.get("start_date", "")
                end   = row.get("end_date",   "")
                if start <= target_str <= end and row.get(weekday, "0") == "1":
                    services.add(row["service_id"])

    if "calendar_dates.txt" in names:
        with zf.open("calendar_dates.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                if row.get("date") != target_str:
                    continue
                sid      = row.get("service_id", "")
                exc_type = row.get("exception_type", "")
                if exc_type == "1":
                    services.add(sid)
                elif exc_type == "2":
                    services.discard(sid)

    return services


def _read_routes(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map route_id → route_short_name."""
    route_map: dict[str, str] = {}
    with zf.open("routes.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            route_map[row["route_id"]] = row.get("route_short_name", "")
    return route_map


def _active_trips(
    zf: zipfile.ZipFile,
    services: set[str],
    route_map: dict[str, str],
) -> dict[str, dict]:
    """Map trip_id → trip row for active services that are real train services."""
    trips: dict[str, dict] = {}
    with zf.open("trips.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            if row.get("service_id") not in services:
                continue
            route_name = route_map.get(row.get("route_id", ""), "").strip().upper()
            if route_name not in _TRAIN_TYPES:
                continue
            row["_route_name"] = route_name
            trips[row["trip_id"]] = row
    return trips


def _extract_arrivals(
    zf: zipfile.ZipFile,
    trips: dict[str, dict],
    date_str: str,
) -> list[Arrival]:
    """Scan stop_times.txt for Gare Centrale rows and build Arrival objects.

    Uses arrival_time (not departure_time) — the moment the train reaches
    the station, not when it departs again.
    """
    arrivals: list[Arrival] = []
    with zf.open("stop_times.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            if row.get("stop_id") != _STOP_ID:
                continue
            trip = trips.get(row.get("trip_id", ""))
            if trip is None:
                continue
            arr_str = row.get("arrival_time", "")
            if not arr_str:
                continue
            arrival = _build_arrival(arr_str, date_str, trip)
            if arrival is not None:
                arrivals.append(arrival)

    return sorted(arrivals, key=lambda a: a.effective_time)


def _build_arrival(time_str: str, date_str: str, trip: dict) -> Arrival | None:
    parts = time_str.split(":")
    if len(parts) < 2:
        return None
    try:
        hour   = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None

    # GTFS allows hour >= 24 for post-midnight continuations of the service day
    day_offset = 0
    if hour >= 24:
        hour -= 24
        day_offset = 1

    try:
        base = datetime.strptime(date_str, "%Y%m%d")
        # is_dst=False: take post-transition (standard) time during DST fall-back
        # to avoid pytz.AmbiguousTimeError on the ambiguous 02:00–03:00 hour.
        dt = _LUX_TZ.localize(
            base.replace(hour=hour, minute=minute, second=0),
            is_dst=False,
        )
        if day_offset:
            dt += timedelta(days=1)
    except (ValueError, TypeError):
        return None

    route_name = trip.get("_route_name", "Train")
    headsign   = (trip.get("trip_headsign") or "").strip()

    # The headsign for a train arriving at Gare Centrale is typically the
    # full route name ending in "Luxembourg, Gare Centrale".
    # Strip that suffix to expose the meaningful origin city.
    if headsign:
        for suffix in (
            "Luxembourg, Gare Centrale",
            ", Gare Centrale",
            "Gare Centrale",
        ):
            headsign = headsign.replace(suffix, "")
        origin = headsign.strip(" -,") or "—"
    else:
        origin = "—"

    return Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=dt,
        identifier=route_name,
        origin=origin,
        status="scheduled",
    )
