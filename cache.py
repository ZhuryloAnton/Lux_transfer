"""Schedule cache: pre-downloaded flights + trains, refreshed every 10 min."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from models import Arrival

logger = logging.getLogger(__name__)


class ScheduleCache:
    """Holds flights and trains for today. Refreshed in background every 10 min."""

    def __init__(self) -> None:
        self._flights: list[Arrival] = []
        self._trains: list[Arrival] = []
        self._flights_ok = False
        self._trains_ok = False
        self._ready = False
        self._lock = asyncio.Lock()

    def is_ready(self) -> bool:
        return self._ready

    async def refresh(
        self,
        flight_source: Any,
        train_source: Any,
    ) -> None:
        """Fetch flights and trains today; update cache. Safe to call from job or on first use."""
        async with self._lock:
            try:
                flights_res, trains_res = await asyncio.gather(
                    flight_source.fetch_today(),
                    train_source.fetch_today(),
                    return_exceptions=True,
                )
                self._flights, self._flights_ok = _unpack(flights_res, "flights/today")
                self._trains, self._trains_ok = _unpack(trains_res, "trains/today")
                self._ready = True
                logger.info(
                    "Schedule cache refreshed: %d flights, %d trains",
                    len(self._flights),
                    len(self._trains),
                )
            except Exception:
                logger.exception("Schedule cache refresh failed")

    def get_flights(self) -> tuple[list[Arrival], bool]:
        return (list(self._flights), self._flights_ok)

    def get_trains(self) -> tuple[list[Arrival], bool]:
        return (list(self._trains), self._trains_ok)

    def get_next_tgv(self) -> Arrival | None:
        """Next TGV from cached trains (today)."""
        from datetime import datetime
        import pytz
        now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
        tgvs = [
            a for a in self._trains
            if a.identifier.upper() == "TGV" and a.effective_time > now
        ]
        return min(tgvs, key=lambda a: a.effective_time) if tgvs else None

    def get_tgvs_today(self) -> list[Arrival]:
        """All TGVs for today from cached trains."""
        from datetime import datetime
        import pytz
        now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
        today = now.date()
        return sorted(
            [a for a in self._trains if a.identifier.upper() == "TGV" and a.effective_time.date() == today],
            key=lambda a: a.effective_time,
        )


def _unpack(result: object, label: str) -> tuple[list[Arrival], bool]:
    if isinstance(result, list):
        return result, True
    logger.error("%s raised: %s", label, result)
    return [], False