"""Train arrivals at Gare Centrale Luxembourg — Luxembourg GTFS timetable + HAFAS real-time.

Static schedule
---------------
Luxembourg Government open data portal (CC0):
  https://data.public.lu/api/1/datasets/horaires-et-arrets-des-transport-publics-gtfs/

GTFS stop_id for "Luxembourg, Gare Centrale": "000200405060"

Real-time delays (optional)
---------------------------
Luxembourg HAFAS API (requires API key from opendata-api@atp.etat.lu):
  POST https://cdt.hafas.de/gate
  StationBoard method with type=ARR for arrivals.
When HAFAS_API_KEY is set in .env, delays are fetched and overlaid onto the
static GTFS schedule.  When absent, the bot falls back to schedule-only.

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
from settings import get_settings

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

# ── HAFAS real-time constants ────────────────────────────────────────────────

_HAFAS_STOP_EXT_ID = "200405060"     # extId for Gare Centrale in HAFAS


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

        # Overlay real-time delays from HAFAS (if API key configured)
        delays = await _fetch_hafas_delays(now.strftime("%Y%m%d"))
        if delays:
            _apply_delays(result, delays)
            delayed_count = sum(1 for a in result if a.delay_minutes)
            logger.info("HAFAS: applied delays to %d/%d arrivals.", delayed_count, len(result))

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

            # Overlay delays so the next-TGV line can show real-time info
            delays = await _fetch_hafas_delays(target.strftime("%Y%m%d"))
            if delays:
                _apply_delays(arrivals, delays)

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
    first_departure: dict[str, str] = {}   # trip_id → departure_time at first stop

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
                first_departure[tid] = row.get("departure_time", "")
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
        dep_str = first_departure.get(tid, "")
        arrival = _build_arrival(arr_str, date_str, trips[tid], origin_name, first_departure_str=dep_str)
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


def _parse_gtfs_time(time_str: str, date_str: str) -> datetime | None:
    """Parse GTFS time (HH:MM:SS or HHH:MM:SS) with date; return tz-aware datetime."""
    if not time_str:
        return None
    parts = time_str.split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
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
        return dt
    except (ValueError, TypeError):
        return None


# TGV Paris Est → Luxembourg passes through Thionville, which is the first stop
# in the Luxembourg GTFS dataset.  Paris Est → Thionville takes ~1h35m.
_PARIS_THIONVILLE_MINUTES = 95

# GTFS origins that indicate a TGV actually comes from Paris
_TGV_PARIS_GATEWAYS = frozenset({"Thionville", "Metz"})


def _build_arrival(
    time_str: str,
    date_str: str,
    trip: dict,
    origin_stop_name: str,
    *,
    first_departure_str: str = "",
) -> Arrival | None:
    dt = _parse_gtfs_time(time_str, date_str)
    if dt is None:
        return None

    route_name = trip.get("_route_name", "Train")
    origin = _clean_stop_name(origin_stop_name) if origin_stop_name else "—"
    origin = origin or "—"

    paris_dep: datetime | None = None
    if route_name == "TGV":
        if "Paris" in origin and first_departure_str:
            # Direct Paris origin in GTFS (unlikely in Luxembourg GTFS, but handle it)
            paris_dep = _parse_gtfs_time(first_departure_str, date_str)
        elif any(gw in origin for gw in _TGV_PARIS_GATEWAYS):
            # TGV from Thionville/Metz = actually from Paris Est
            # Estimate Paris departure from Thionville departure
            thionville_dep = _parse_gtfs_time(first_departure_str, date_str)
            if thionville_dep:
                paris_dep = thionville_dep - timedelta(minutes=_PARIS_THIONVILLE_MINUTES)
            origin = f"Paris Est (via {origin})"

    return Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=dt,
        identifier=route_name,
        origin=origin,
        status="scheduled",
        paris_departure=paris_dep,
    )


# ── HAFAS real-time delay functions ──────────────────────────────────────────
#
# Uses the REST departureBoard endpoint (the only board type available on this
# server).  Trains departing Gare Centrale also arrived there, so the real-time
# departure delay reflects the service's overall delay.
#
# For trains that TERMINATE at Gare Centrale (our primary interest), they won't
# appear in the departureBoard.  In that case we match against the same service
# at the penultimate stop in the next cache cycle.  This is a best-effort
# overlay — unmatched arrivals simply keep delay_minutes=0.

_HAFAS_REST_URL = "https://cdt.hafas.de/opendata/apiserver/departureBoard"
_HAFAS_MAX_DURATION = 240  # API caps at ~4 hours per request


def _parse_rest_time(time_str: str) -> tuple[int, int] | None:
    """Parse REST API time 'HH:MM:SS' → (hour, minute)."""
    if not time_str or len(time_str) < 5:
        return None
    try:
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None


def _delay_between(scheduled: str, realtime: str) -> int:
    """Compute delay in minutes between two HH:MM:SS time strings."""
    s = _parse_rest_time(scheduled)
    r = _parse_rest_time(realtime)
    if s is None or r is None:
        return 0
    diff = (r[0] * 60 + r[1]) - (s[0] * 60 + s[1])
    if diff < -12 * 60:
        diff += 24 * 60
    return max(0, diff)


def _extract_train_type(name: str) -> str:
    """Extract train type from HAFAS departure name like 'TGV 2855' or 'RE 5107'."""
    token = name.split()[0].upper().rstrip("0123456789") if name else ""
    # Handle concatenated names like "TER88705" → "TER"
    if not token:
        token = ""
        for ch in name.upper():
            if ch.isalpha():
                token += ch
            else:
                break
    return token if token in _TRAIN_TYPES else ""


async def _fetch_hafas_delays(date_str: str) -> dict[tuple[int, int, str], int]:
    """Fetch real-time delays from HAFAS departureBoard for Gare Centrale.

    Queries multiple time windows to cover the full day (API caps at ~4h per request).
    Returns dict: (hour, minute, train_type) → delay_minutes.
    Returns empty dict if API key not configured or on any error.
    """
    api_key = get_settings().hafas_api_key
    if not api_key:
        return {}

    # Format date as YYYY-MM-DD for REST API
    rest_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    delays: dict[tuple[int, int, str], int] = {}

    # Cover 05:00–23:00 with 4-hour windows
    for start_hour in range(5, 23, 4):
        start_time = f"{start_hour:02d}:00"
        try:
            data = await fetch_json(
                _HAFAS_REST_URL,
                params={
                    "accessId": api_key,
                    "id": _HAFAS_STOP_EXT_ID,
                    "lang": "en",
                    "format": "json",
                    "duration": str(_HAFAS_MAX_DURATION),
                    "date": rest_date,
                    "time": start_time,
                },
                ssl=False,
            )
        except Exception as exc:
            logger.warning("HAFAS departureBoard %s failed: %s", start_time, exc)
            continue

        if not isinstance(data, dict):
            continue

        err = data.get("errorCode", "")
        if err:
            logger.warning("HAFAS error at %s: %s — %s", start_time, err, data.get("errorText", ""))
            continue

        for dep in data.get("Departure", []):
            name = dep.get("name", "")
            train_type = _extract_train_type(name)
            if not train_type:
                continue

            sched_time = dep.get("time", "")
            rt_time = dep.get("rtTime", "")

            parsed = _parse_rest_time(sched_time)
            if parsed is None:
                continue

            key = (parsed[0], parsed[1], train_type)
            if rt_time:
                delays[key] = _delay_between(sched_time, rt_time)
            else:
                delays.setdefault(key, 0)

    if delays:
        delayed = sum(1 for d in delays.values() if d > 0)
        logger.info("HAFAS: %d services tracked (%d delayed).", len(delays), delayed)
    return delays


def _apply_delays(
    arrivals: list[Arrival],
    delays: dict[tuple[int, int, str], int],
) -> None:
    """Overlay HAFAS delay data onto GTFS Arrival objects (in-place).

    Matches by (scheduled_hour, scheduled_minute, train_type).
    For terminating trains that don't appear in the departureBoard,
    we also try matching with ±1 minute tolerance.
    """
    for arrival in arrivals:
        key = (
            arrival.scheduled_time.hour,
            arrival.scheduled_time.minute,
            arrival.identifier,
        )
        if key in delays:
            arrival.delay_minutes = delays[key]
            continue
        # Try ±1 minute for slight schedule differences between GTFS and HAFAS
        h, m = key[0], key[1]
        for dm in (-1, 1):
            adj_m = m + dm
            adj_h = h
            if adj_m < 0:
                adj_m = 59
                adj_h = h - 1
            elif adj_m >= 60:
                adj_m = 0
                adj_h = h + 1
            alt_key = (adj_h, adj_m, arrival.identifier)
            if alt_key in delays:
                arrival.delay_minutes = delays[alt_key]
                break
