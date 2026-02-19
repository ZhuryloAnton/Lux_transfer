from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta
from typing import Any

import pytz

from src.models import Arrival, TransportType
from src.services.base import BaseDataSource, DataSourceError
from src.utils.cache import cached
from src.utils.http import fetch_json, get_session

logger = logging.getLogger(__name__)

LUX_TZ = pytz.timezone("Europe/Luxembourg")

GARE_STOP_ID = "000200405060"
GTFS_DATASET_URL = (
    "https://data.public.lu/api/1/datasets/"
    "horaires-et-arrets-des-transport-publics-gtfs/"
)

TRAIN_TYPES = frozenset({"ICE", "TGV", "IC", "EC", "RE", "RB", "TER", "IR"})


class TrainDataSource(BaseDataSource):
    """Gare Centrale Luxembourg train arrivals from official GTFS data.

    Uses the Luxembourg government's official GTFS static timetable
    published on data.public.lu. This contains real CFL/SNCF/DB
    scheduled train times — updated weekly.
    """

    def __init__(self) -> None:
        super().__init__("trains")
        self._gtfs_cache: zipfile.ZipFile | None = None

    async def _get_gtfs(self) -> zipfile.ZipFile:
        if self._gtfs_cache is not None:
            return self._gtfs_cache
        data = await fetch_json(GTFS_DATASET_URL, ssl=False)
        if not isinstance(data, dict):
            raise DataSourceError("GTFS dataset API returned unexpected format")
        resources = data.get("resources", [])
        url = None
        for r in resources:
            if r.get("format", "").lower() == "zip" and r.get("url"):
                url = r["url"]
                break
        if not url:
            raise DataSourceError("No GTFS zip found in dataset resources")
        session = await get_session()
        async with session.get(url, ssl=False) as resp:
            resp.raise_for_status()
            content = await resp.read()
        self._gtfs_cache = zipfile.ZipFile(io.BytesIO(content))
        return self._gtfs_cache

    async def _fetch_for_date(self, target: datetime) -> list[dict]:
        zf = await self._get_gtfs()
        target_str = target.strftime("%Y%m%d")
        weekday = target.strftime("%A").lower()

        with zf.open("calendar.txt") as f:
            cal_reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            active_services: set[str] = set()
            for row in cal_reader:
                start = row.get("start_date", "")
                end = row.get("end_date", "")
                if start <= target_str <= end and row.get(weekday, "0") == "1":
                    active_services.add(row["service_id"])

        if not active_services:
            raise DataSourceError(f"No active services for {target_str}")

        with zf.open("routes.txt") as f:
            route_reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            route_map: dict[str, str] = {}
            for r in route_reader:
                route_map[r["route_id"]] = r.get("route_short_name", "")

        with zf.open("trips.txt") as f:
            trip_reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            active_trips: dict[str, dict] = {}
            for t in trip_reader:
                if t.get("service_id") in active_services:
                    active_trips[t["trip_id"]] = t

        with zf.open("stop_times.txt") as f:
            st_reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            results: list[dict] = []
            for st in st_reader:
                if st.get("stop_id") != GARE_STOP_ID:
                    continue
                trip = active_trips.get(st.get("trip_id", ""))
                if not trip:
                    continue
                arr_time = st.get("arrival_time", "")
                if not arr_time:
                    continue
                route_name = route_map.get(trip.get("route_id", ""), "")
                if route_name and route_name not in TRAIN_TYPES:
                    continue
                results.append({
                    "arrival_time": arr_time,
                    "date": target_str,
                    "route_name": route_name,
                    "trip_headsign": trip.get("trip_headsign", ""),
                })

        if not results:
            raise DataSourceError(f"No train arrivals at Gare Centrale for {target_str}")
        return results

    @cached("trains_today")
    async def fetch_raw(self) -> Any:
        return await self._fetch_for_date(datetime.now(tz=LUX_TZ))

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Full day tomorrow."""
        try:
            raw = await self._fetch_for_date(
                datetime.now(tz=LUX_TZ) + timedelta(days=1),
            )
        except DataSourceError as exc:
            logger.warning("No tomorrow train data: %s", exc)
            return []
        parsed = await self.parse(raw)
        return sorted(parsed, key=lambda a: a.effective_time)

    async def parse(self, raw: Any) -> list[Arrival]:
        if not isinstance(raw, list):
            return []
        arrivals: list[Arrival] = []
        for entry in raw:
            a = self._parse_gtfs_entry(entry)
            if a is not None:
                arrivals.append(a)
        return sorted(arrivals, key=lambda a: a.effective_time)

    async def validate(self, items: list[Arrival]) -> list[Arrival]:
        now = datetime.now(tz=LUX_TZ)
        return [a for a in items if a.effective_time >= now]

    @staticmethod
    def _parse_gtfs_entry(entry: dict) -> Arrival | None:
        time_str = entry.get("arrival_time", "")
        date_str = entry.get("date", "")
        if not time_str or not date_str:
            return None
        parts = time_str.split(":")
        if len(parts) < 2:
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            day_offset = 0
            if hour >= 24:
                hour -= 24
                day_offset = 1
            base = datetime.strptime(date_str, "%Y%m%d")
            dt = LUX_TZ.localize(base.replace(hour=hour, minute=minute, second=0))
            if day_offset:
                dt += timedelta(days=day_offset)
        except (ValueError, TypeError):
            return None

        route = entry.get("route_name", "")
        headsign = entry.get("trip_headsign", "")
        if not route:
            return None

        origin = headsign.replace(", Gare", "").replace(" Gare", "") if headsign else "Unknown"

        return Arrival(
            transport_type=TransportType.TRAIN,
            scheduled_time=dt,
            identifier=route,
            origin=origin,
            status="scheduled",
        )
