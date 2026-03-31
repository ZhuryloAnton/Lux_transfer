"""Train arrivals at Gare Centrale Luxembourg — GTFS open data (no API key).

Uses Luxembourg public transport GTFS from data.public.lu / openov.
Set optional GTFS_URL in .env to override the default feed URL.
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

from taxibot.core.http import fetch_bytes
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


def _read_zip_csv(zip_file: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    """Read a CSV file from the GTFS zip; return list of dicts (header as keys)."""
    try:
        with zip_file.open(name) as f:
            text = f.read().decode("utf-8-sig")
    except KeyError:
        return []
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


class GTFSTrainSource:
    """Train data from a GTFS zip (Luxembourg open data). No API key required.
    Optional get_delay(trip_id, stop_id) -> delay_minutes adds real-time delays from GTFS-RT.
    """

    def __init__(
        self,
        gtfs_url: str = "",
        get_delay: Callable[[str, str], int | None] | None = None,
    ) -> None:
        self._url = (gtfs_url or _DEFAULT_GTFS_URL).strip()
        self._get_delay = get_delay
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
        # Find Luxembourg Gare Centrale stop_id (main rail station)
        lux_stop_id = None
        candidates: list[tuple[str, int]] = []  # (stop_id, priority: lower = better)
        for s in stops:
            name = (s.get("stop_name") or "").lower().strip()
            if "luxembourg" not in name:
                continue
            sid = s.get("stop_id")
            if not sid:
                continue
            # Prefer station-like names (gare, central) over e.g. "Luxembourg, Rue X"
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
        # route_id -> route_short_name (include rail=2, or missing type)
        route_info = {}
        for r in routes:
            rid = r.get("route_id")
            if not rid:
                continue
            rtype = r.get("route_type")
            if rtype is not None and str(rtype).strip() and str(rtype) not in ("2", "100"):
                continue
            route_info[rid] = (r.get("route_short_name") or r.get("route_long_name") or "Train").strip()
        # service_id -> valid on date (simplified: use calendar_dates if present, else calendar)
        service_ids: set[str] = set()
        for cd in calendar_dates:
            if cd.get("exception_type") == "1":
                service_ids.add(cd.get("service_id", ""))
        if not service_ids and calendar:
            # Use calendar.txt for weekday validity (simplified)
            now = datetime.now(tz=_LUX_TZ)
            wd = now.weekday()  # 0=Monday
            for c in calendar:
                start = c.get("start_date", "")
                end = c.get("end_date", "")
                if start and end and start <= today <= end:
                    if wd == 0 and c.get("monday") == "1":
                        service_ids.add(c.get("service_id", ""))
                    elif wd == 1 and c.get("tuesday") == "1":
                        service_ids.add(c.get("service_id", ""))
                    elif wd == 2 and c.get("wednesday") == "1":
                        service_ids.add(c.get("service_id", ""))
                    elif wd == 3 and c.get("thursday") == "1":
                        service_ids.add(c.get("service_id", ""))
                    elif wd == 4 and c.get("friday") == "1":
                        service_ids.add(c.get("service_id", ""))
                    elif wd == 5 and c.get("saturday") == "1":
                        service_ids.add(c.get("service_id", ""))
                    elif wd == 6 and c.get("sunday") == "1":
                        service_ids.add(c.get("service_id", ""))
        # trip_id -> (route_id, service_id)
        trip_route: dict[str, str] = {}
        trip_service: dict[str, str] = {}
        for t in trips:
            tid = t.get("trip_id")
            if tid:
                trip_route[tid] = t.get("route_id", "")
                trip_service[tid] = t.get("service_id", "")
        # First stop of each trip for origin
        trip_first_stop: dict[str, str] = {}
        for st in stop_times:
            tid = st.get("trip_id")
            if not tid or tid in trip_first_stop:
                continue
            trip_first_stop[tid] = st.get("stop_id", "")
        stop_names = {s.get("stop_id", ""): (s.get("stop_name") or "").strip() for s in stops}
        # For calendar_dates we have exact dates; for calendar we only have today's services
        # Simplify: build list for "today" and "tomorrow" by checking calendar_dates for each
        def _svc(s: str) -> str:
            return (s or "").strip()

        def _norm_date(s: str) -> str:
            """Normalise to YYYYMMDD (feed may use YYYYMMDD or YYYY-MM-DD)."""
            s = (s or "").strip().replace("-", "")
            return s

        def valid_services_for_date(d: datetime) -> set[str]:
            ds = d.strftime("%Y%m%d")
            wd = d.weekday()
            out: set[str] = set()
            # 1) Services from calendar.txt: weekday + date in range
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
            # 2) Add calendar_dates exception_type 1 (service added on this date)
            for cd in calendar_dates:
                if _norm_date(str(cd.get("date", ""))) != ds:
                    continue
                if str(cd.get("exception_type", "")).strip() == "1":
                    out.add(_svc(cd.get("service_id", "")))
            # 3) Remove calendar_dates exception_type 2 (service removed on this date)
            for cd in calendar_dates:
                if _norm_date(str(cd.get("date", ""))) != ds:
                    continue
                if str(cd.get("exception_type", "")).strip() == "2":
                    out.discard(_svc(cd.get("service_id", "")))
            return out

        # Build arrivals for today and tomorrow
        result: dict[str, list[tuple[str, str, str, str]]] = {}
        for label, d in (("today", datetime.now(tz=_LUX_TZ)), ("tomorrow", datetime.now(tz=_LUX_TZ) + timedelta(days=1))):
            svc = valid_services_for_date(d)
            day_start = d.replace(hour=0, minute=0, second=0, microsecond=0)
            list_for_day = []
            for st in stop_times:
                if st.get("stop_id") != lux_stop_id:
                    continue
                tid = st.get("trip_id", "")
                if trip_service.get(tid) not in svc:
                    continue
                rid = trip_route.get(tid, "")
                if rid not in route_info:
                    continue
                arr_time = st.get("arrival_time") or st.get("departure_time", "")
                if not arr_time:
                    continue
                first_stop = trip_first_stop.get(tid, "")
                list_for_day.append((arr_time, tid, rid, first_stop))
            result[label] = list_for_day

        self._cache = {
            "route_info": route_info,
            "stop_names": stop_names,
            "lux_stop_id": lux_stop_id,
            "arrivals_today": result.get("today", []),
            "arrivals_tomorrow": result.get("tomorrow", []),
        }
        self._cache_date = today
        logger.info("GTFS loaded: %d today, %d tomorrow arrivals at Luxembourg", len(self._cache["arrivals_today"]), len(self._cache["arrivals_tomorrow"]))
        return self._cache

    def _arrivals_for_date(self, data: dict, day: datetime) -> list[Arrival]:
        now = datetime.now(tz=_LUX_TZ)
        tomorrow_date = (now + timedelta(days=1)).date()
        which = "arrivals_tomorrow" if day.date() == tomorrow_date else "arrivals_today"
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        route_info = data.get("route_info", {})
        stop_names = data.get("stop_names", {})
        lux_stop_id = data.get("lux_stop_id", "")
        rows = data.get(which, [])
        out: list[Arrival] = []
        for arr_time, _tid, rid, first_stop_id in rows:
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
            origin = stop_names.get(first_stop_id, "—").strip() or "—"
            out.append(Arrival(
                transport_type=TransportType.TRAIN,
                scheduled_time=dt,
                identifier=name,
                origin=origin,
                status="scheduled",
                delay_minutes=delay_minutes,
            ))
        return sorted(out, key=lambda a: a.effective_time)

    async def fetch_today(self) -> list[Arrival]:
        data = await self._load_gtfs()
        if not data:
            return []
        now = datetime.now(tz=_LUX_TZ)
        arr = self._arrivals_for_date(data, now)
        return [a for a in arr if a.effective_time >= now]

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
        today = self._arrivals_for_date(data, now)
        tgvs = [a for a in today if a.identifier == "TGV" and a.effective_time > now]
        if tgvs:
            return min(tgvs, key=lambda a: a.effective_time)
        tomorrow = self._arrivals_for_date(data, now + timedelta(days=1))
        tgvs = [a for a in tomorrow if a.identifier == "TGV"]
        return min(tgvs, key=lambda a: a.effective_time) if tgvs else None
