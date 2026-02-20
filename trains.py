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

from models import Arrival, TransportType
from http_client import fetch_bytes, fetch_json

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")

# ── Constants ─────────────────────────────────────────────────────────────────

_GTFS_DATASET_URL = (
    "https://data.public.lu/api/1/datasets/"
    "horaires-et-arrets-des-transport-publics-gtfs/"
)

# stop_id in GTFS stops.txt for "Luxembourg, Gare Centrale"
_STOP_ID = "000200405060"

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
    """Scan stop_times.txt for trains whose FINAL stop is Gare Centrale.

    Only trains that terminate at Gare Centrale are true arrivals (passengers
    exiting and needing taxis).  Trains that originate or pass through are
    excluded — they are departures, not arrivals.

    Two-pass approach:
      1. Find the maximum stop_sequence per trip and the Gare Centrale row.
      2. Keep only trips where Gare Centrale has the highest stop_sequence.
    """
    gc_rows: dict[str, dict] = {}          # trip_id → stop_times row at Gare Centrale
    trip_max_seq: dict[str, int] = {}      # trip_id → highest stop_sequence seen
    trip_first: dict[str, tuple[int, str]] = {}  # trip_id → (min_seq, stop_id)

    with zf.open("stop_times.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            tid = row.get("trip_id", "")
            if tid not in trips:
                continue
            seq = int(row.get("stop_sequence", 0))
            if tid not in trip_max_seq or seq > trip_max_seq[tid]:
                trip_max_seq[tid] = seq
            if tid not in trip_first or seq < trip_first[tid][0]:
                trip_first[tid] = (seq, row.get("stop_id", ""))
            if row.get("stop_id") == _STOP_ID:
                gc_rows[tid] = row

    stop_names = _read_stop_names(zf)

    arrivals: list[Arrival] = []
    for tid, row in gc_rows.items():
        gc_seq = int(row.get("stop_sequence", 0))
        if gc_seq != trip_max_seq.get(tid, -1):
            continue                       # not the final stop → skip
        arr_str = row.get("arrival_time", "")
        if not arr_str:
            continue
        origin_sid = trip_first.get(tid, (0, ""))[1]
        origin_name = stop_names.get(origin_sid, "")
        arrival = _build_arrival(arr_str, date_str, trips[tid], origin_name)
        if arrival is not None:
            arrivals.append(arrival)

    return sorted(arrivals, key=lambda a: a.effective_time)


def _read_stop_names(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map stop_id → stop_name from stops.txt."""
    names: dict[str, str] = {}
    with zf.open("stops.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            names[row.get("stop_id", "")] = row.get("stop_name", "")
    return names


def _clean_stop_name(name: str) -> str:
    """Strip common GTFS suffixes like ', Gare' to keep origin labels short."""
    for suffix in (", Gare Centrale", ", Gare", ", Hauptbahnhof", ", Hbf"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _build_arrival(
    time_str: str, date_str: str, trip: dict, origin_stop_name: str
) -> Arrival | None:
    parts = time_str.split(":")
    if len(parts) < 2:
        return None
    try:
        hour   = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None

    day_offset = 0
    if hour >= 24:
        hour -= 24
        day_offset = 1

    try:
        base = datetime.strptime(date_str, "%Y%m%d")
        dt = _LUX_TZ.localize(
            base.replace(hour=hour, minute=minute, second=0),
            is_dst=False,
        )
        if day_offset:
            dt += timedelta(days=1)
    except (ValueError, TypeError):
        return None

    route_name = trip.get("_route_name", "Train")
    origin = _clean_stop_name(origin_stop_name) if origin_stop_name else "—"

    return Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=dt,
        identifier=route_name,
        origin=origin or "—",
        status="scheduled",
    )
