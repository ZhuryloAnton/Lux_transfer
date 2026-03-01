"""Train arrivals at Gare Centrale Luxembourg — mobiliteit.lu HAFAS API.

Data source
-----------
Luxembourg public transport HAFAS API (cdt.hafas.de):
  arrivalBoard  — scheduled + real-time arrivals at a stop
  departureBoard — fallback if arrivalBoard is unavailable

API key must be requested at opendata-api@verkeiersverbond.lu
and stored in .env as MOBILITEIT_API_KEY.

Stop ID for "Luxembourg, Gare Centrale": 200405060
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytz

from models import Arrival, TransportType
from http_client import fetch_json

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")

_API_BASE = "https://cdt.hafas.de/opendata/apiserver"
_STOP_ID = "200405060"

_TRAIN_CATEGORIES = frozenset({
    "ICE", "TGV", "IC", "EC", "RE", "RB", "TER", "IR", "CRE", "CRN",
})


class TrainDataSource:
    """Train arrivals at Gare Centrale via the mobiliteit.lu HAFAS API."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    # ── Public interface (same contract as before) ────────────────────────────

    async def fetch_today(self) -> list[Arrival]:
        """Return train arrivals at Gare Centrale from now until end of day."""
        now = datetime.now(tz=_LUX_TZ)
        remaining_mins = _minutes_until_eod(now)
        if remaining_mins < 10:
            remaining_mins = 10
        arrivals = await self._fetch_arrivals(now, duration=remaining_mins)
        result = [a for a in arrivals if a.effective_time >= now]
        logger.info("HAFAS today: %d arrivals at Gare Centrale.", len(result))
        return result

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Return all train arrivals at Gare Centrale for tomorrow."""
        now = datetime.now(tz=_LUX_TZ)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        arrivals = await self._fetch_arrivals(tomorrow, duration=1439)
        logger.info("HAFAS tomorrow: %d arrivals at Gare Centrale.", len(arrivals))
        return arrivals

    async def get_next_tgv(self) -> Arrival | None:
        """Return the next TGV arriving at Gare Centrale with Paris departure time."""
        now = datetime.now(tz=_LUX_TZ)
        arrivals = await self._fetch_arrivals(now, duration=1439)
        tgvs = [
            a for a in arrivals
            if a.identifier == "TGV" and a.effective_time > now
        ]
        if not tgvs:
            return None

        tgv = min(tgvs, key=lambda a: a.effective_time)

        if tgv.journey_ref:
            tgv.paris_departure = await self._fetch_paris_departure(tgv.journey_ref)

        return tgv

    # ── Journey detail (Paris stop lookup) ─────────────────────────────────────

    async def _fetch_paris_departure(self, ref: str) -> datetime | None:
        """Fetch journey stops and return the departure time from Paris."""
        if not self._api_key or not ref:
            return None
        params = {
            "accessId": self._api_key,
            "ref": ref,
            "format": "json",
            "lang": "en",
        }
        url = f"{_API_BASE}/journeyDetail"
        try:
            data = await fetch_json(url, params=params, ssl=False)
        except Exception as exc:
            logger.warning("HAFAS journeyDetail failed: %s", exc)
            return None

        if not isinstance(data, dict):
            return None

        stops = _extract_journey_stops(data)
        return _find_paris_departure(stops)

    # ── API call ──────────────────────────────────────────────────────────────

    async def _fetch_arrivals(
        self, start: datetime, *, duration: int = 1439,
    ) -> list[Arrival]:
        """Query the HAFAS arrivalBoard for Gare Centrale.

        Falls back to departureBoard if arrivalBoard is not available.
        """
        if not self._api_key:
            logger.error("MOBILITEIT_API_KEY not set — cannot fetch trains.")
            return []

        date_str = start.strftime("%Y-%m-%d")
        time_str = start.strftime("%H:%M")

        params = {
            "accessId": self._api_key,
            "id": _STOP_ID,
            "date": date_str,
            "time": time_str,
            "duration": str(min(duration, 1439)),
            "format": "json",
            "lang": "en",
        }

        for endpoint, resp_key, arrival_mode in (
            ("arrivalBoard",   "Arrival",   True),
            ("departureBoard", "Departure", False),
        ):
            url = f"{_API_BASE}/{endpoint}"
            try:
                data = await fetch_json(url, params=params, ssl=False)
            except Exception as exc:
                logger.warning("HAFAS %s failed: %s", endpoint, exc)
                continue

            if not isinstance(data, dict):
                logger.warning("HAFAS %s: unexpected response type %s", endpoint, type(data))
                continue

            if data.get("errorCode"):
                logger.warning(
                    "HAFAS %s error: %s — %s",
                    endpoint, data.get("errorCode"), data.get("errorText"),
                )
                continue

            entries = _extract_entries(data, resp_key)
            if entries is not None:
                arrivals = _parse_entries(entries, is_arrival=arrival_mode)
                logger.info(
                    "HAFAS %s returned %d train arrivals.", endpoint, len(arrivals),
                )
                return arrivals

            logger.info("HAFAS %s: no '%s' entries in response.", endpoint, resp_key)

        logger.error("HAFAS: could not fetch train arrivals from any endpoint.")
        return []


# ── Response parsing ──────────────────────────────────────────────────────────

def _extract_entries(data: object, key: str) -> list[dict] | None:
    """Extract the Arrival or Departure list from the HAFAS response."""
    if not isinstance(data, dict):
        return None
    entries = data.get(key)
    if entries is None:
        return None
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return None
    return entries


def _parse_entries(entries: list[dict], *, is_arrival: bool) -> list[Arrival]:
    """Convert HAFAS entries to Arrival objects, filtering for trains only."""
    arrivals: list[Arrival] = []
    for entry in entries:
        a = _parse_one(entry, is_arrival=is_arrival)
        if a is not None:
            arrivals.append(a)
    return sorted(arrivals, key=lambda a: a.effective_time)


def _parse_one(entry: dict, *, is_arrival: bool) -> Arrival | None:
    if not isinstance(entry, dict):
        return None

    product = entry.get("Product") or entry.get("product")
    if not isinstance(product, dict):
        return None

    category = (
        product.get("catOutS")
        or product.get("catOut", "").strip()
        or product.get("catIn", "").strip()
        or ""
    ).strip().upper()

    if category not in _TRAIN_CATEGORIES:
        return None

    sched_date = entry.get("date", "")
    sched_time = entry.get("time", "")
    if not sched_date or not sched_time:
        return None

    sched_dt = _parse_hafas_dt(sched_date, sched_time)
    if sched_dt is None:
        return None

    # Real-time data (if available)
    rt_date = entry.get("rtDate", "")
    rt_time = entry.get("rtTime", "")
    rt_dt = _parse_hafas_dt(rt_date, rt_time) if rt_date and rt_time else None

    delay = 0
    if rt_dt and sched_dt:
        delay = max(0, int((rt_dt - sched_dt).total_seconds() / 60))

    origin_field = "origin" if is_arrival else "direction"
    origin = _clean_name(entry.get(origin_field, ""))

    ref_data = entry.get("JourneyDetailRef") or {}
    journey_ref = ref_data.get("ref", "") if isinstance(ref_data, dict) else ""

    return Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=sched_dt,
        identifier=category,
        origin=origin or "—",
        status="scheduled",
        delay_minutes=delay,
        journey_ref=journey_ref,
    )


def _parse_hafas_dt(date_str: str, time_str: str) -> datetime | None:
    """Parse HAFAS date + time strings into a tz-aware datetime."""
    try:
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0

        day_offset = 0
        if hour >= 24:
            hour -= 24
            day_offset = 1

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dt = dt.replace(hour=hour, minute=minute, second=second)
        dt = _LUX_TZ.localize(dt, is_dst=False)
        if day_offset:
            dt += timedelta(days=1)
        return dt
    except (ValueError, TypeError, IndexError):
        return None


def _extract_journey_stops(data: dict) -> list[dict]:
    """Extract stop list from a HAFAS journeyDetail response."""
    stops_wrapper = data.get("Stops") or data.get("stops") or {}
    if isinstance(stops_wrapper, dict):
        stops = stops_wrapper.get("Stop") or stops_wrapper.get("stop") or []
    elif isinstance(stops_wrapper, list):
        stops = stops_wrapper
    else:
        stops = []
    if isinstance(stops, dict):
        stops = [stops]
    return stops if isinstance(stops, list) else []


def _find_paris_departure(stops: list[dict]) -> datetime | None:
    """Find the departure time from a Paris station in the journey stops."""
    for stop in stops:
        name = (stop.get("name") or stop.get("Name") or "").lower()
        if "paris" not in name:
            continue
        dep_date = stop.get("depDate", "") or stop.get("depDate", "")
        dep_time = stop.get("depTime", "") or stop.get("depTime", "")
        if dep_date and dep_time:
            return _parse_hafas_dt(dep_date, dep_time)
        arr_date = stop.get("arrDate", "")
        arr_time = stop.get("arrTime", "")
        if arr_date and arr_time:
            return _parse_hafas_dt(arr_date, arr_time)
    return None


def _clean_name(name: str) -> str:
    """Strip common station suffixes to keep origin labels short."""
    if not name:
        return "—"
    for suffix in (
        ", Gare Centrale", ", Gare", ", Hauptbahnhof", ", Hbf",
        " Hbf", " Hauptbahnhof",
    ):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _minutes_until_eod(now: datetime) -> int:
    """Minutes from *now* until 23:59 on the same day."""
    eod = now.replace(hour=23, minute=59, second=59)
    return max(1, int((eod - now).total_seconds() / 60))
