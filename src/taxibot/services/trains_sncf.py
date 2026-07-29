"""TGV arrivals at Luxembourg from SNCF open data. No API key required.

Why this source exists
----------------------
Luxembourg's own feeds do not carry TGV:

* ``stboard.exe`` / HAFAS ``departureBoard`` return only RB / RE / IC.
* The national GTFS had a TGV route whose service dates stopped on 2026-07-15,
  and the 2026-07-21 release dropped the route entirely.

SNCF publishes TGV itself, openly and keylessly, via transport.data.gouv.fr:

* static GTFS  — https://eu.ftp.opendatasoft.com/sncf/plandata/...zip  (~5 MB, daily)
* GTFS-RT      — https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates

Identifying a TGV here
----------------------
``route_short_name`` is an internal code (``803B``, ``071A``), **not** ``"TGV"``,
so route names cannot be used to detect TGVs. What marks them is the *stop point*:
SNCF models each mode as its own stop under a shared station, giving
``StopPoint:OCETGV INOUI-82001000`` alongside ``OCETrain TER-…`` and
``OCECar à réservation-…``. We locate the TGV stop point and treat everything
calling there as a TGV, then set ``identifier="TGV"`` ourselves.

Only **terminating** arrivals are returned. Trips that merely call at Luxembourg
on the way elsewhere — and the outbound services that *start* here — are not taxi
demand.

Note on service dates: TGV Paris–Luxembourg is suspended for summer 2026. The
feed contains no Luxembourg TGV before 2026-08-24; an empty result before then is
correct, not a failure.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytz

from taxibot.core.http import fetch_bytes
from taxibot.models import Arrival, TransportType

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")

_SNCF_GTFS_URL = (
    "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"
)
_SNCF_GTFS_RT_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"

# SNCF's station id for Luxembourg. Used only to narrow the stop-point search.
_LUX_STATION_CODE = "82001000"

# Suffixes SNCF appends to origin names that add nothing for a taxi driver.
_ORIGIN_SUFFIXES = (" Saint-Charles", " Saint-Roch", "-Ville", " Est", " Gare")


def _clean_origin(name: str) -> str:
    """Shorten an SNCF station label, but never to nothing.

    ``"Paris Est"`` must stay ``"Paris Est"`` — that is the actual station and
    dropping the suffix would be wrong. Only trims when something is left over
    that is still recognisable.
    """
    for suffix in _ORIGIN_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix) + 3:
            trimmed = name[: -len(suffix)].strip()
            if suffix == " Est":          # Paris Est / Paris Nord are distinct stations
                return name
            if trimmed:
                return trimmed
    return name


def _gtfs_time_to_delta(value: str) -> timedelta | None:
    """Parse a GTFS ``HH:MM:SS`` offset, which may exceed 24h (e.g. ``25:10:00``).

    GTFS expresses times as an offset from *noon minus 12h* of the service day,
    so a train after midnight is ``24:30:00`` rather than ``00:30:00``. Treating
    it as a clock time would place it on the wrong day.
    """
    parts = (value or "").strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return timedelta(hours=h, minutes=m, seconds=s)


def _stream_csv(z: zipfile.ZipFile, name: str) -> Iterator[tuple[list[str], dict[str, int]]]:
    """Stream a CSV member as ``(row, column_index)`` without materialising it.

    Deliberately not ``DictReader`` into a list: ``calendar_dates.txt`` is ~271k
    rows and ``stop_times.txt`` ~420k, and building a dict per row pushed peak
    RSS to ~410 MB — absurd for a process whose whole retained index is under a
    megabyte, and enough to get OOM-killed in a small container.
    """
    try:
        handle = z.open(name)
    except KeyError:
        logger.warning("SNCF GTFS: %s missing from archive", name)
        return
    with handle:
        reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
        header = next(reader, None)
        if header is None:
            return
        index = {h.strip(): i for i, h in enumerate(header)}
        for row in reader:
            yield row, index


def _cell(row: list[str], index: dict[str, int], column: str) -> str:
    i = index.get(column)
    if i is None or i >= len(row):
        return ""
    return row[i]


def _parse_gtfs(raw: bytes) -> dict[str, Any]:
    """Build the TGV arrival index from a GTFS zip.

    Runs in a worker thread — see :meth:`SNCFTrainSource._load`. Pure CPU work
    over ~420k ``stop_times`` rows, so it must never touch the event loop.
    """
    with zipfile.ZipFile(io.BytesIO(raw), "r") as z:
        # The TGV stop point at Luxembourg, found by pattern rather than a
        # hardcoded id so a re-issued id does not silently empty the feature.
        tgv_stop_id = ""
        stop_names: dict[str, str] = {}
        for row, idx in _stream_csv(z, "stops.txt"):
            sid = _cell(row, idx, "stop_id")
            if not sid:
                continue
            stop_names[sid] = _cell(row, idx, "stop_name").strip()
            if _LUX_STATION_CODE in sid and "TGV" in sid.upper():
                tgv_stop_id = sid
        if not tgv_stop_id:
            logger.warning("SNCF GTFS: no TGV stop point found for Luxembourg (%s)", _LUX_STATION_CODE)
            return {}

        # Pass 1 over stop_times: which trips call at Luxembourg, and when.
        # Narrowing to these ~200 trips first is what keeps every later structure
        # small — collecting first/last for all ~48k trips was the memory hog.
        lux_arrival: dict[str, str] = {}
        for row, idx in _stream_csv(z, "stop_times.txt"):
            if _cell(row, idx, "stop_id") == tgv_stop_id:
                tid = _cell(row, idx, "trip_id")
                if tid:
                    lux_arrival[tid] = _cell(row, idx, "arrival_time")

        if not lux_arrival:
            logger.warning("SNCF GTFS: no trips call at %s", tgv_stop_id)
            return {}

        # Pass 2: first and last stop, for those trips only. Re-reading the file
        # costs CPU in a worker thread; holding 48k tuples cost hundreds of MB.
        first: dict[str, tuple[int, str, str]] = {}
        last: dict[str, tuple[int, str]] = {}
        for row, idx in _stream_csv(z, "stop_times.txt"):
            tid = _cell(row, idx, "trip_id")
            if tid not in lux_arrival:
                continue
            try:
                seq = int(_cell(row, idx, "stop_sequence") or 0)
            except ValueError:
                continue
            sid = _cell(row, idx, "stop_id")
            if tid not in first or seq < first[tid][0]:
                first[tid] = (seq, sid, _cell(row, idx, "departure_time"))
            if tid not in last or seq > last[tid][0]:
                last[tid] = (seq, sid)

        # Only the services behind those trips matter.
        trip_service: dict[str, str] = {}
        for row, idx in _stream_csv(z, "trips.txt"):
            tid = _cell(row, idx, "trip_id")
            if tid in lux_arrival:
                trip_service[tid] = _cell(row, idx, "service_id")
        wanted_services = set(trip_service.values())

        # This feed uses calendar_dates exclusively (there is no calendar.txt),
        # listing every operating day as exception_type=1. Filtering to the ~135
        # relevant services turns 271k rows into a few thousand kept dates.
        service_dates: dict[str, set[str]] = {}
        for row, idx in _stream_csv(z, "calendar_dates.txt"):
            if _cell(row, idx, "exception_type") != "1":
                continue
            sid = _cell(row, idx, "service_id")
            if sid in wanted_services:
                service_dates.setdefault(sid, set()).add(_cell(row, idx, "date"))

    # Keep only trips that TERMINATE at the Luxembourg TGV stop point.
    index: dict[str, list[tuple[str, str, str, str]]] = {}
    for tid, arr_time in lux_arrival.items():
        if last.get(tid, (0, ""))[1] != tgv_stop_id:
            continue
        origin_seq = first.get(tid)
        if origin_seq is None or origin_seq[1] == tgv_stop_id:
            continue  # starts here: an outbound departure, not an arrival
        origin_name = _clean_origin(stop_names.get(origin_seq[1], "?"))
        entry = (arr_time, origin_name, origin_seq[2], tid)
        for date in service_dates.get(trip_service.get(tid, ""), ()):
            index.setdefault(date, []).append(entry)

    for rows in index.values():
        rows.sort()
    dates = sorted(index)
    logger.info(
        "SNCF GTFS: %d terminating TGV arrivals at Luxembourg across %d dates (%s..%s)",
        sum(len(v) for v in index.values()), len(dates),
        dates[0] if dates else "-", dates[-1] if dates else "-",
    )
    return {"by_date": index, "tgv_stop_id": tgv_stop_id}


def _parse_rt(raw: bytes, tgv_stop_id: str) -> dict[str, int]:
    """Decode GTFS-RT trip updates into ``trip_id -> delay minutes``.

    Also CPU-bound (protobuf), so it runs in a thread like the static parse.
    Prefers the delay reported *at Luxembourg*; falls back to the trip's last
    reported stop, which is the best available proxy.
    """
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError:
        logger.warning("gtfs-realtime-bindings not installed — TGV delays unavailable")
        return {}

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(raw)
    except Exception as exc:
        logger.warning("SNCF GTFS-RT parse failed: %s", exc)
        return {}

    delays: dict[str, int] = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        tid = update.trip.trip_id
        if not tid:
            continue
        chosen: int | None = None
        fallback: int | None = None
        for stu in update.stop_time_update:
            secs = None
            if stu.HasField("arrival") and stu.arrival.HasField("delay"):
                secs = stu.arrival.delay
            elif stu.HasField("departure") and stu.departure.HasField("delay"):
                secs = stu.departure.delay
            if secs is None:
                continue
            if stu.stop_id == tgv_stop_id:
                chosen = secs
                break
            fallback = secs
        secs = chosen if chosen is not None else fallback
        if secs:
            # Negative = running early. Arrival.delay_minutes is "minutes late",
            # so clamp: an early train is not a delay.
            delays[tid] = max(0, round(secs / 60))
    logger.info("SNCF GTFS-RT: %d trip updates, %d with a delay", len(feed.entity), len(delays))
    return delays


class SNCFTrainSource:
    """Terminating TGV arrivals at Luxembourg, with real-time delays."""

    def __init__(
        self,
        gtfs_url: str = "",
        gtfs_rt_url: str = "",
        cache_hours: int = 6,
        rt_cache_seconds: int = 300,
    ) -> None:
        self._url = (gtfs_url or _SNCF_GTFS_URL).strip()
        self._rt_url = (gtfs_rt_url or _SNCF_GTFS_RT_URL).strip()
        self._cache_ttl = timedelta(hours=cache_hours)
        self._rt_ttl = timedelta(seconds=rt_cache_seconds)

        self._data: dict[str, Any] = {}
        self._loaded_at: datetime | None = None
        self._load_lock = asyncio.Lock()

        self._delays: dict[str, int] = {}
        self._delays_at: datetime | None = None

    # ── Loading ───────────────────────────────────────────────────────────────

    async def _load(self) -> dict[str, Any]:
        """Return the parsed index, downloading at most once per TTL.

        The lock matters: three view builders can ask for trains concurrently, and
        without it each would kick off its own 5 MB download and multi-second
        parse. Whoever gets the lock does the work; the rest re-check and reuse it.
        """
        now = datetime.now(tz=_LUX_TZ)
        if self._data and self._loaded_at and now - self._loaded_at < self._cache_ttl:
            return self._data

        async with self._load_lock:
            # Re-check: another waiter may have loaded it while we queued.
            now = datetime.now(tz=_LUX_TZ)
            if self._data and self._loaded_at and now - self._loaded_at < self._cache_ttl:
                return self._data
            try:
                raw = await fetch_bytes(self._url)
            except Exception as exc:
                logger.warning("SNCF GTFS download failed: %s", exc)
                return self._data  # stale beats nothing
            try:
                # to_thread, not a bare call: parsing ~420k rows takes seconds and
                # would otherwise freeze every other user's request for that long.
                parsed = await asyncio.to_thread(_parse_gtfs, raw)
            except Exception as exc:
                logger.exception("SNCF GTFS parse failed: %s", exc)
                return self._data
            if parsed:
                self._data = parsed
                self._loaded_at = datetime.now(tz=_LUX_TZ)
            return self._data

    async def _current_delays(self) -> dict[str, int]:
        now = datetime.now(tz=_LUX_TZ)
        if self._delays_at and now - self._delays_at < self._rt_ttl:
            return self._delays
        stop_id = self._data.get("tgv_stop_id", "")
        try:
            raw = await fetch_bytes(self._rt_url)
            self._delays = await asyncio.to_thread(_parse_rt, raw, stop_id)
        except Exception as exc:
            logger.warning("SNCF GTFS-RT fetch failed: %s", exc)
            return self._delays
        self._delays_at = now
        return self._delays

    # ── Public interface ──────────────────────────────────────────────────────

    async def fetch_today(self) -> list[Arrival]:
        now = datetime.now(tz=_LUX_TZ)
        arrivals = await self._arrivals_for(now)
        return [a for a in arrivals if a.effective_time >= now]

    async def fetch_tomorrow(self) -> list[Arrival]:
        return await self._arrivals_for(datetime.now(tz=_LUX_TZ) + timedelta(days=1))

    async def next_arrival(self, after: datetime | None = None) -> Arrival | None:
        """The next TGV arrival on any date the feed covers, however far out.

        ``_next_tgv_from_lists`` only inspects today and tomorrow, so during a long
        suspension it returns ``None`` and the TGV block can only say "no TGV" —
        indistinguishable from a broken feed. Paris–Luxembourg is currently paused
        for 26 days, so scanning the whole index lets the message name the real
        resume date, read from the feed rather than hardcoded where it would rot.

        Dates are scanned in order and the first match wins, so the cost is one
        day's rows in the normal case.
        """
        now = after or datetime.now(tz=_LUX_TZ)
        data = await self._load()
        today_key = now.strftime("%Y%m%d")
        for date in sorted(data.get("by_date", {})):
            if date < today_key:
                continue
            try:
                day = _LUX_TZ.localize(datetime.strptime(date, "%Y%m%d"))
            except ValueError:
                continue
            for arrival in await self._arrivals_for(day):
                if arrival.effective_time >= now:
                    return arrival
        return None

    async def _arrivals_for(self, day: datetime) -> list[Arrival]:
        data = await self._load()
        rows = data.get("by_date", {}).get(day.strftime("%Y%m%d"), [])
        if not rows:
            return []
        delays = await self._current_delays()

        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        out: list[Arrival] = []
        for arr_time, origin, origin_dep, tid in rows:
            offset = _gtfs_time_to_delta(arr_time)
            if offset is None:
                continue
            scheduled = day_start + offset

            paris_departure = None
            dep_offset = _gtfs_time_to_delta(origin_dep)
            if dep_offset is not None:
                # The real origin departure, straight from the feed — no need for
                # the Thionville-minus-95-minutes estimate the GTFS path uses.
                paris_departure = day_start + dep_offset
                if paris_departure > scheduled:
                    # Overnight service: the origin departure is the previous day.
                    paris_departure -= timedelta(days=1)

            out.append(
                Arrival(
                    transport_type=TransportType.TRAIN,
                    scheduled_time=scheduled,
                    identifier="TGV",
                    origin=origin,
                    status="scheduled",
                    delay_minutes=delays.get(tid, 0),
                    paris_departure=paris_departure,
                )
            )
        out.sort(key=lambda a: a.effective_time)
        return out
