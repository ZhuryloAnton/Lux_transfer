"""Train arrivals at Gare Centrale Luxembourg — GTFS open data + HAFAS real-time delays.

Uses Luxembourg public transport GTFS from data.public.lu / openov.
Set optional GTFS_URL in .env to override the default feed URL.

Real-time delays (optional): when HAFAS_API_KEY is set, delays are fetched
from the HAFAS REST departureBoard and overlaid onto the static schedule.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pytz

from taxibot.core.http import fetch_bytes, fetch_json
from taxibot.models import Arrival, TransportType

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")
# Project root for resolving relative GTFS paths (e.g. files/gtfs.zip)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Default: Luxembourg GTFS from OpenOV (same data as data.public.lu)
# Override with GTFS_URL in .env if needed
_DEFAULT_GTFS_URL = "http://openov.lu/data/gtfs/gtfs-openov-lu.zip"

# Stop name patterns for Luxembourg main station (Gare Centrale)
_LUXEMBOURG_STOP_NAMES = ("luxembourg", "gare centrale", "gare centraal", "central")

# route_short_name values that identify real train services
_TRAIN_TYPES = frozenset({
    "ICE", "TGV", "IC", "EC", "RE", "RB", "TER", "IR", "CRE", "CRN",
})

# TGV Paris Est → Luxembourg passes through Thionville (first stop in Luxembourg GTFS).
# Paris Est → Thionville takes ~1h35m.
_PARIS_THIONVILLE_MINUTES = 95
_TGV_PARIS_GATEWAYS = frozenset({"Thionville", "Metz"})

# ── HAFAS real-time constants ────────────────────────────────────────────────
_HAFAS_STOP_EXT_ID = "200405060"     # extId for Gare Centrale in HAFAS
_HAFAS_REST_URL = "https://cdt.hafas.de/opendata/apiserver/departureBoard"
_HAFAS_MAX_DURATION = 240  # API caps at ~4 hours per request


def _build_datetime(date: datetime, time_str: str) -> datetime:
    """Build tz-aware datetime from date and GTFS arrival_time (HH:MM:SS)."""
    parts = time_str.strip().split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    day_offset = 0
    if h >= 24:
        h -= 24
        day_offset = 1
    dt = date.replace(hour=h, minute=m, second=s, microsecond=0)
    if day_offset:
        dt += timedelta(days=1)
    return _LUX_TZ.localize(dt, is_dst=None)


def _parse_gtfs_time(time_str: str, date_str: str) -> datetime | None:
    """Parse GTFS time (HH:MM:SS) with date string (YYYYMMDD); return tz-aware datetime."""
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


def _read_zip_csv(zip_file: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    """Read a CSV file from the GTFS zip; return list of dicts (header as keys)."""
    try:
        with zip_file.open(name) as f:
            text = f.read().decode("utf-8-sig")
    except KeyError:
        return []
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _clean_stop_name(name: str) -> str:
    """Strip common GTFS suffixes like ', Gare' to keep origin labels short."""
    for suffix in (", Gare Centrale", ", Gare", ", Hauptbahnhof", ", Hbf"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# ── HAFAS delay functions ────────────────────────────────────────────────────

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
    if not token:
        token = ""
        for ch in name.upper():
            if ch.isalpha():
                token += ch
            else:
                break
    return token if token in _TRAIN_TYPES else ""


async def _fetch_hafas_delays(hafas_api_key: str, date_str: str) -> dict[tuple[int, int, str], int]:
    """Fetch real-time delays from HAFAS departureBoard for Gare Centrale.

    Queries multiple time windows to cover the full day (API caps at ~4h per request).
    Returns dict: (hour, minute, train_type) → delay_minutes.
    """
    if not hafas_api_key:
        return {}

    # Format date as YYYY-MM-DD for REST API
    rest_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    delays: dict[tuple[int, int, str], int] = {}

    for start_hour in range(5, 23, 4):
        start_time = f"{start_hour:02d}:00"
        try:
            data = await fetch_json(
                _HAFAS_REST_URL,
                params={
                    "accessId": hafas_api_key,
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
    """Overlay HAFAS delay data onto Arrival objects (in-place).

    Matches by (scheduled_hour, scheduled_minute, train_type).
    Also tries ±1 minute tolerance for slight schedule differences.
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


class GTFSTrainSource:
    """Train data from a GTFS zip (Luxembourg open data). No API key required.

    Optional get_delay(trip_id, stop_id) -> delay_minutes adds GTFS-RT delays.
    Optional hafas_api_key enables HAFAS REST delays (more reliable).
    """

    def __init__(
        self,
        gtfs_url: str = "",
        get_delay: Callable[[str, str], int | None] | None = None,
        hafas_api_key: str = "",
    ) -> None:
        self._url = (gtfs_url or _DEFAULT_GTFS_URL).strip()
        self._get_delay = get_delay
        self._hafas_api_key = hafas_api_key
        self._cache: dict[str, Any] = {}
        self._cache_date: str | None = None

    async def _load_gtfs(self) -> dict[str, Any]:
        """Download and parse GTFS; cache for 12 hours."""
        today = datetime.now(tz=_LUX_TZ).strftime("%Y-%m-%d")
        if self._cache and self._cache_date == today:
            return self._cache
        try:
            if self._url.startswith("file://"):
                raw = Path(self._url[7:].lstrip("/")).read_bytes()
            elif "://" not in self._url:
                p = Path(self._url)
                if not p.is_absolute():
                    p = _PROJECT_ROOT / p
                raw = p.read_bytes()
            else:
                raw = await fetch_bytes(self._url)
        except Exception as e:
            logger.warning("GTFS load failed: %s", e)
            return {}
        try:
            with zipfile.ZipFile(io.BytesIO(raw), "r") as z:
                stops = _read_zip_csv(z, "stops.txt")
                routes = _read_zip_csv(z, "routes.txt")
                trips = _read_zip_csv(z, "trips.txt")
                stop_times = _read_zip_csv(z, "stop_times.txt")
                calendar = _read_zip_csv(z, "calendar.txt")
                calendar_dates = _read_zip_csv(z, "calendar_dates.txt")
        except Exception as e:
            logger.warning("GTFS parse failed: %s", e)
            return {}

        # Find Luxembourg Gare Centrale stop_id
        lux_stop_id = None
        candidates: list[tuple[str, int]] = []
        for s in stops:
            name = (s.get("stop_name") or "").lower().strip()
            if "luxembourg" not in name:
                continue
            sid = s.get("stop_id")
            if not sid:
                continue
            if "gare" in name or name == "luxembourg":
                priority = 0 if "gare" in name else 1
                candidates.append((sid, priority))
            elif "central" in name or "centrale" in name or "centraal" in name:
                candidates.append((sid, 0))
            else:
                candidates.append((sid, 2))
        if candidates:
            candidates.sort(key=lambda x: x[1])
            lux_stop_id = candidates[0][0]
        if not lux_stop_id:
            logger.warning("GTFS: Luxembourg station not found in stops")
            return {}

        # route_id -> route_short_name (rail=2 or missing type)
        route_info = {}
        for r in routes:
            rid = r.get("route_id")
            if not rid:
                continue
            rtype = r.get("route_type")
            if rtype is not None and str(rtype).strip() and str(rtype) not in ("2", "100"):
                continue
            route_info[rid] = (r.get("route_short_name") or r.get("route_long_name") or "Train").strip()

        # trip_id -> (route_id, service_id)
        trip_route: dict[str, str] = {}
        trip_service: dict[str, str] = {}
        for t in trips:
            tid = t.get("trip_id")
            if tid:
                trip_route[tid] = t.get("route_id", "")
                trip_service[tid] = t.get("service_id", "")

        stop_names = {s.get("stop_id", ""): (s.get("stop_name") or "").strip() for s in stops}

        def _svc(s: str) -> str:
            return (s or "").strip()

        def _norm_date(s: str) -> str:
            s = (s or "").strip().replace("-", "")
            return s

        def valid_services_for_date(d: datetime) -> set[str]:
            ds = d.strftime("%Y%m%d")
            wd = d.weekday()
            out: set[str] = set()
            for c in calendar:
                start, end = _norm_date(c.get("start_date", "")), _norm_date(c.get("end_date", ""))
                if not start or not end or not (start <= ds <= end):
                    continue
                day_ok = (
                    (wd == 0 and str(c.get("monday", "")).strip() == "1")
                    or (wd == 1 and str(c.get("tuesday", "")).strip() == "1")
                    or (wd == 2 and str(c.get("wednesday", "")).strip() == "1")
                    or (wd == 3 and str(c.get("thursday", "")).strip() == "1")
                    or (wd == 4 and str(c.get("friday", "")).strip() == "1")
                    or (wd == 5 and str(c.get("saturday", "")).strip() == "1")
                    or (wd == 6 and str(c.get("sunday", "")).strip() == "1")
                )
                if day_ok:
                    out.add(_svc(c.get("service_id", "")))
            for cd in calendar_dates:
                if _norm_date(str(cd.get("date", ""))) != ds:
                    continue
                if str(cd.get("exception_type", "")).strip() == "1":
                    out.add(_svc(cd.get("service_id", "")))
            for cd in calendar_dates:
                if _norm_date(str(cd.get("date", ""))) != ds:
                    continue
                if str(cd.get("exception_type", "")).strip() == "2":
                    out.discard(_svc(cd.get("service_id", "")))
            return out

        # Build arrivals for today and tomorrow — only TERMINATING trains
        result: dict[str, list[tuple[str, str, str, str, str]]] = {}
        for label, d in (("today", datetime.now(tz=_LUX_TZ)), ("tomorrow", datetime.now(tz=_LUX_TZ) + timedelta(days=1))):
            svc = valid_services_for_date(d)
            day_start = d.replace(hour=0, minute=0, second=0, microsecond=0)

            # First pass: find max stop_sequence, first stop, and GC row per trip
            gc_rows: dict[str, dict] = {}       # trip_id → stop_times row at GC
            trip_max_seq: dict[str, int] = {}   # trip_id → highest stop_sequence
            trip_first: dict[str, tuple[int, str]] = {}  # trip_id → (min_seq, stop_id)
            first_departure: dict[str, str] = {}  # trip_id → departure_time at first stop

            for st in stop_times:
                tid = st.get("trip_id", "")
                if trip_service.get(tid) not in svc:
                    continue
                rid = trip_route.get(tid, "")
                if rid not in route_info:
                    continue
                # Filter to real train types
                rname = route_info.get(rid, "").strip().upper()
                if rname not in _TRAIN_TYPES:
                    continue

                seq = int(st.get("stop_sequence", 0))
                if tid not in trip_max_seq or seq > trip_max_seq[tid]:
                    trip_max_seq[tid] = seq
                if tid not in trip_first or seq < trip_first[tid][0]:
                    trip_first[tid] = (seq, st.get("stop_id", ""))
                    first_departure[tid] = st.get("departure_time", "")
                if st.get("stop_id") == lux_stop_id:
                    gc_rows[tid] = st

            # Second pass: keep only trips where GC is the FINAL stop (terminating)
            list_for_day: list[tuple[str, str, str, str, str]] = []
            for tid, row in gc_rows.items():
                gc_seq = int(row.get("stop_sequence", 0))
                if gc_seq != trip_max_seq.get(tid, -1):
                    continue  # not the final stop → skip (pass-through train)
                arr_time = row.get("arrival_time") or row.get("departure_time", "")
                if not arr_time:
                    continue
                rid = trip_route.get(tid, "")
                first_stop_id = trip_first.get(tid, (0, ""))[1]
                dep_str = first_departure.get(tid, "")
                list_for_day.append((arr_time, tid, rid, first_stop_id, dep_str))

            result[label] = list_for_day

        self._cache = {
            "route_info": route_info,
            "stop_names": stop_names,
            "lux_stop_id": lux_stop_id,
            "arrivals_today": result.get("today", []),
            "arrivals_tomorrow": result.get("tomorrow", []),
        }
        self._cache_date = today
        logger.info(
            "GTFS loaded: %d today, %d tomorrow terminating arrivals at Luxembourg",
            len(self._cache["arrivals_today"]),
            len(self._cache["arrivals_tomorrow"]),
        )
        return self._cache

    def _arrivals_for_date(self, data: dict, day: datetime) -> list[Arrival]:
        now = datetime.now(tz=_LUX_TZ)
        tomorrow_date = (now + timedelta(days=1)).date()
        which = "arrivals_tomorrow" if day.date() == tomorrow_date else "arrivals_today"
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        date_str = day.strftime("%Y%m%d")
        route_info = data.get("route_info", {})
        stop_names = data.get("stop_names", {})
        lux_stop_id = data.get("lux_stop_id", "")
        rows = data.get(which, [])
        out: list[Arrival] = []
        for arr_time, _tid, rid, first_stop_id, dep_str in rows:
            try:
                dt = _build_datetime(day_start, arr_time)
            except (ValueError, IndexError, TypeError):
                continue
            delay_minutes = 0
            if self._get_delay and lux_stop_id:
                d = self._get_delay(_tid, lux_stop_id)
                if d is not None and d > 0:
                    delay_minutes = d
            name = route_info.get(rid, "Train")
            if name.upper().startswith("TGV"):
                name = "TGV"
            origin_raw = stop_names.get(first_stop_id, "—").strip() or "—"
            origin = _clean_stop_name(origin_raw)

            # TGV Paris origin mapping
            paris_dep: datetime | None = None
            if name == "TGV":
                if "Paris" in origin and dep_str:
                    paris_dep = _parse_gtfs_time(dep_str, date_str)
                elif any(gw in origin for gw in _TGV_PARIS_GATEWAYS):
                    thionville_dep = _parse_gtfs_time(dep_str, date_str)
                    if thionville_dep:
                        paris_dep = thionville_dep - timedelta(minutes=_PARIS_THIONVILLE_MINUTES)
                    origin = f"Paris Est (via {origin})"

            out.append(Arrival(
                transport_type=TransportType.TRAIN,
                scheduled_time=dt,
                identifier=name,
                origin=origin,
                status="scheduled",
                delay_minutes=delay_minutes,
                paris_departure=paris_dep,
            ))
        return sorted(out, key=lambda a: a.effective_time)

    async def fetch_today(self) -> list[Arrival]:
        data = await self._load_gtfs()
        if not data:
            return []
        now = datetime.now(tz=_LUX_TZ)
        arr = self._arrivals_for_date(data, now)
        result = [a for a in arr if a.effective_time >= now]

        # Overlay HAFAS real-time delays
        delays = await _fetch_hafas_delays(self._hafas_api_key, now.strftime("%Y%m%d"))
        if delays:
            _apply_delays(result, delays)
            delayed_count = sum(1 for a in result if a.delay_minutes)
            logger.info("HAFAS: applied delays to %d/%d arrivals.", delayed_count, len(result))

        return result

    async def fetch_tomorrow(self) -> list[Arrival]:
        data = await self._load_gtfs()
        if not data:
            return []
        tomorrow = datetime.now(tz=_LUX_TZ) + timedelta(days=1)
        return self._arrivals_for_date(data, tomorrow)

    async def get_next_train(self) -> Arrival | None:
        """Next train at Gare Centrale whenever it is (today, else tomorrow)."""
        data = await self._load_gtfs()
        if not data:
            return None
        now = datetime.now(tz=_LUX_TZ)
        today = self._arrivals_for_date(data, now)
        after_now = [a for a in today if a.effective_time > now]
        if after_now:
            return min(after_now, key=lambda a: a.effective_time)
        tomorrow = self._arrivals_for_date(data, now + timedelta(days=1))
        if tomorrow:
            return min(tomorrow, key=lambda a: a.effective_time)
        return None

    async def get_next_tgv(self) -> Arrival | None:
        data = await self._load_gtfs()
        if not data:
            return None
        now = datetime.now(tz=_LUX_TZ)

        for offset in (0, 1):
            target = now + timedelta(days=offset)
            arr = self._arrivals_for_date(data, target)

            # Overlay delays for TGV too
            delays = await _fetch_hafas_delays(self._hafas_api_key, target.strftime("%Y%m%d"))
            if delays:
                _apply_delays(arr, delays)

            tgvs = [a for a in arr if a.identifier == "TGV" and a.effective_time > now]
            if tgvs:
                return min(tgvs, key=lambda a: a.effective_time)
        return None