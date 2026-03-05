"""Train arrivals from Open Data API (configurable endpoint).

Uses OPEN_DATA_API URL to fetch train departures/arrivals. Same list is used
for all trains; get_next_tgv filters to TGV only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pytz

from taxibot.core.http import fetch_json
from taxibot.models import Arrival, TransportType

logger = logging.getLogger(__name__)

_LUX_TZ = pytz.timezone("Europe/Luxembourg")


def _parse_time(value: Any) -> datetime | None:
    """Parse API time to tz-aware Luxembourg datetime. Handles ISO, HH:MM, timestamp."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=_LUX_TZ)
        except (ValueError, OSError):
            return None
    s = str(value).strip()
    if not s:
        return None
    # ISO 8601 (YYYY-MM-DDTHH:MM:SS or with Z/+00:00)
    if "T" in s:
        try:
            s = s.replace("Z", "+00:00").strip()
            if "+" in s or s.endswith("-00:00"):
                from datetime import timezone
                dt = datetime.fromisoformat(s)
            else:
                dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
                dt = _LUX_TZ.localize(dt)
            return dt.astimezone(_LUX_TZ)
        except Exception:
            pass
    # HH:MM or HH:MM:SS (use today)
    if ":" in s:
        parts = s.replace(".", ":").split(":")
        try:
            h, m = int(parts[0]), int(parts[1])
            sec = int(parts[2]) if len(parts) > 2 else 0
            if h >= 24:
                h -= 24
            now = datetime.now(tz=_LUX_TZ)
            dt = now.replace(hour=h, minute=m, second=sec, microsecond=0)
            if dt < now and h < 12:
                dt += timedelta(days=1)
            return dt
        except (ValueError, IndexError):
            pass
    return None


def _parse_hafas_date_time(date_str: str, time_str: str) -> datetime | None:
    """HAFAS: date (YYYY-MM-DD) + time (HH:MM:SS or HH:MM), 24+ = next day."""
    if not date_str or not time_str:
        return None
    try:
        parts = str(time_str).strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        sec = int(parts[2]) if len(parts) > 2 else 0
        day_offset = 0
        if h >= 24:
            h -= 24
            day_offset = 1
        dt = datetime.strptime(str(date_str).strip()[:10], "%Y-%m-%d")
        dt = dt.replace(hour=h, minute=m, second=sec, microsecond=0)
        if day_offset:
            dt += timedelta(days=1)
        return _LUX_TZ.localize(dt, is_dst=None)
    except (ValueError, TypeError, IndexError):
        return None


def _parse_departure(item: dict, now: datetime) -> Arrival | None:
    """Map one API item to Arrival. Supports HAFAS (mobiliteit.lu) and generic APIs."""
    dt = None
    delay_minutes = 0

    # HAFAS mobiliteit.lu: date + time, optional rtDate + rtTime for delay
    date_s = item.get("date") or item.get("Date")
    time_s = item.get("time") or item.get("Time")
    if date_s and time_s:
        dt = _parse_hafas_date_time(str(date_s), str(time_s))
        rt_date = item.get("rtDate") or item.get("RtDate")
        rt_time = item.get("rtTime") or item.get("RtTime")
        if dt and rt_date and rt_time:
            rt_dt = _parse_hafas_date_time(str(rt_date), str(rt_time))
            if rt_dt and rt_dt > dt:
                delay_minutes = max(0, int((rt_dt - dt).total_seconds() / 60))

    if dt is None:
        time_keys = (
            "departureTime", "scheduledDepartureTime", "departure_time",
            "scheduledTime", "time", "timestamp", "aimed_departure_time",
        )
        for k in time_keys:
            dt = _parse_time(item.get(k))
            if dt is not None:
                break
        for k in ("delay", "delayMinutes", "delay_minutes", "departureDelay"):
            v = item.get(k)
            if v is not None:
                try:
                    d = int(float(v))
                    if d > 0:
                        delay_minutes = d if d < 120 else d // 60
                    break
                except (ValueError, TypeError):
                    pass

    if dt is None:
        return None

    # Line/category: HAFAS has Product (list or dict) and ProductAtStop with name, catOut
    identifier = "Train"
    product = item.get("Product") or item.get("product")
    if isinstance(product, list) and product and isinstance(product[0], dict):
        product = product[0]
    product_at_stop = item.get("ProductAtStop") or item.get("productAtStop")
    if isinstance(product_at_stop, dict):
        for k in ("name", "catOutS", "catOut", "line"):
            v = product_at_stop.get(k)
            if v is not None and str(v).strip():
                identifier = str(v).strip()
                break
    if identifier == "Train" and isinstance(product, dict):
        for k in ("name", "catOutS", "catOut", "catIn"):
            v = product.get(k)
            if v is not None and str(v).strip():
                identifier = str(v).strip()
                break
    if identifier == "Train":
        for k in ("name", "lineName", "line", "routeShortName", "route", "trainType", "category"):
            v = item.get(k)
            if v is not None and str(v).strip():
                identifier = str(v).strip()
                break

    origin = "—"
    for k in ("direction", "Direction", "destination", "origin", "from", "stopPointName"):
        v = item.get(k)
        if v is not None and str(v).strip():
            origin = str(v).strip()
            break

    return Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=dt,
        identifier=identifier,
        origin=origin,
        status="scheduled",
        delay_minutes=delay_minutes,
    )


def _extract_list(data: Any) -> list[dict]:
    """Get list of departure items from API response. HAFAS uses 'Departure'."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("Departure", "departure", "departures", "Departures", "data", "results", "trains", "stopTimes"):
        val = data.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            return [val]
    return []


class OpenDataTrainSource:
    """Train data from Open Data API. Fetches once per request; get_next_tgv filters to TGV."""

    def __init__(self, api_url: str = "") -> None:
        self._url = (api_url or "").strip().rstrip("/")

    async def _fetch_departures(self, date: datetime | None = None) -> list[Arrival]:
        """Fetch departures from API. If date is set, request that day (for tomorrow)."""
        if not self._url:
            logger.warning("OPEN_DATA_API URL not set")
            return []
        use_ssl = "cdt.hafas.de" not in self._url
        params: dict[str, str] = {}
        if date is not None:
            # HAFAS-style APIs often accept date/time so we get that day's departures
            params["date"] = date.strftime("%Y-%m-%d")
            params["time"] = "00:00"
        try:
            data = await fetch_json(self._url, params=params or None, ssl=use_ssl)
        except Exception as e:
            logger.warning("Open Data API fetch failed: %s", e)
            return []
        items = _extract_list(data)
        now = datetime.now(tz=_LUX_TZ)
        arrivals: list[Arrival] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            a = _parse_departure(item, now)
            if a is not None:
                arrivals.append(a)
        arrivals.sort(key=lambda x: x.effective_time)
        logger.info("Open Data API: %d train departures", len(arrivals))
        return arrivals

    async def fetch_today(self) -> list[Arrival]:
        """Trains for today (effective_time >= now, same calendar day)."""
        arrivals = await self._fetch_departures(date=None)
        now = datetime.now(tz=_LUX_TZ)
        today = now.date()
        return [a for a in arrivals if a.effective_time >= now and a.effective_time.date() == today]

    async def fetch_tomorrow(self) -> list[Arrival]:
        """Trains for tomorrow — request API for tomorrow's date when supported, else filter from default response."""
        tomorrow_dt = datetime.now(tz=_LUX_TZ) + timedelta(days=1)
        tomorrow_start = tomorrow_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        arrivals = await self._fetch_departures(date=tomorrow_start)
        tomorrow = tomorrow_dt.date()
        result = [a for a in arrivals if a.effective_time.date() == tomorrow]
        if not result and arrivals:
            # API may ignore date param; use default response and filter
            arrivals_default = await self._fetch_departures(date=None)
            result = [a for a in arrivals_default if a.effective_time.date() == tomorrow]
        return result

    async def get_next_train(self) -> Arrival | None:
        """Next train (any type) — today or tomorrow."""
        arrivals = await self._fetch_departures()
        now = datetime.now(tz=_LUX_TZ)
        future = [a for a in arrivals if a.effective_time > now]
        if not future:
            return None
        return min(future, key=lambda a: a.effective_time)

    async def get_next_tgv(self) -> Arrival | None:
        """Next TGV only — same API, filter to line name containing TGV."""
        arrivals = await self._fetch_departures()
        now = datetime.now(tz=_LUX_TZ)
        tgvs = [a for a in arrivals if a.effective_time > now and "TGV" in a.identifier.upper()]
        if not tgvs:
            return None
        return min(tgvs, key=lambda a: a.effective_time)
