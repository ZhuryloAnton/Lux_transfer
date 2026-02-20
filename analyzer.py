"""Build Report objects from raw arrival lists.

No event logic. Pure data analysis: windowing, peak detection,
time-block grouping, and recommendation generation.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

import pytz

from bot.models import Arrival, DemandPeak, Report, SourceStatus, TimeBlock

LUX_TZ = pytz.timezone("Europe/Luxembourg")

_TIME_BLOCKS = [
    ("Early Morning (05–08)", 5, 8),
    ("Morning (08–12)", 8, 12),
    ("Afternoon (12–17)", 12, 17),
    ("Evening (17–21)", 17, 21),
    ("Night (21–00)", 21, 24),
]


def build_now_report(
    flights: list[Arrival],
    trains: list[Arrival],
    *,
    flights_ok: bool,
    trains_ok: bool,
) -> Report:
    now = datetime.now(tz=LUX_TZ)
    window_end = now + timedelta(hours=3)

    f = _in_window(flights, now, window_end)
    t = _in_window(trains, now, window_end)

    next_flight = _first_after(flights, now) if not f and flights_ok else None
    next_train = _first_after(trains, now) if not t and trains_ok else None

    return Report(
        generated_at=now,
        window_start=now,
        window_end=window_end,
        flights=f,
        trains=t,
        flight_peaks=_peaks(f, "Airport"),
        train_peaks=_peaks(t, "Gare Centrale"),
        recommendations=_recs_now(f, t, next_flight, next_train),
        flights_status=SourceStatus.OK if flights_ok else SourceStatus.UNAVAILABLE,
        trains_status=SourceStatus.OK if trains_ok else SourceStatus.UNAVAILABLE,
        next_flight=next_flight,
        next_train=next_train,
    )


def build_tomorrow_report(
    morning_flights: list[Arrival],
    trains: list[Arrival],
    *,
    flights_ok: bool,
    trains_ok: bool,
) -> Report:
    now = datetime.now(tz=LUX_TZ)
    tomorrow_start = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    tomorrow_end = tomorrow_start.replace(hour=23, minute=59, second=59)

    all_arrivals = sorted(
        morning_flights + trains, key=lambda a: a.effective_time
    )
    blocks = _group_into_blocks(all_arrivals, tomorrow_start)

    return Report(
        generated_at=now,
        window_start=tomorrow_start,
        window_end=tomorrow_end,
        flights=sorted(morning_flights, key=lambda a: a.effective_time),
        trains=sorted(trains, key=lambda a: a.effective_time),
        flight_peaks=_peaks(morning_flights, "Airport"),
        train_peaks=_peaks(trains, "Gare Centrale"),
        recommendations=_recs_tomorrow(morning_flights, trains, blocks),
        flights_status=SourceStatus.OK if flights_ok else SourceStatus.UNAVAILABLE,
        trains_status=SourceStatus.OK if trains_ok else SourceStatus.UNAVAILABLE,
        time_blocks=blocks,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _in_window(
    arrivals: list[Arrival], start: datetime, end: datetime
) -> list[Arrival]:
    return sorted(
        [a for a in arrivals if start <= a.effective_time <= end],
        key=lambda a: a.effective_time,
    )


def _first_after(arrivals: list[Arrival], after: datetime) -> Arrival | None:
    future = [a for a in arrivals if a.effective_time > after]
    return min(future, key=lambda a: a.effective_time) if future else None


def _peaks(arrivals: list[Arrival], location: str) -> list[DemandPeak]:
    if not arrivals:
        return []
    slots: Counter[str] = Counter()
    for a in arrivals:
        slot = a.effective_time.strftime("%H:") + (
            "00" if a.effective_time.minute < 30 else "30"
        )
        slots[slot] += 1
    return [
        DemandPeak(time_slot=s, count=c, location=location)
        for s, c in slots.most_common(3)
        if c >= 2
    ]


def _group_into_blocks(
    arrivals: list[Arrival], day: datetime
) -> list[TimeBlock]:
    blocks: list[TimeBlock] = []
    for label, sh, eh in _TIME_BLOCKS:
        block_start = day.replace(hour=sh, minute=0, second=0)
        block_end = day.replace(hour=min(eh, 23), minute=59, second=59)
        items = [a for a in arrivals if block_start <= a.effective_time <= block_end]
        blocks.append(TimeBlock(label=label, start_hour=sh, end_hour=eh, arrivals=items))
    return blocks


def _recs_now(
    flights: list[Arrival],
    trains: list[Arrival],
    next_flight: Arrival | None,
    next_train: Arrival | None,
) -> list[str]:
    recs: list[str] = []
    if flights:
        f0 = flights[0].effective_time.strftime("%H:%M")
        f1 = flights[-1].effective_time.strftime("%H:%M")
        recs.append(f"{f0}–{f1} → Airport ({len(flights)} flights)")
    if trains:
        t0 = trains[0].effective_time.strftime("%H:%M")
        t1 = trains[-1].effective_time.strftime("%H:%M")
        recs.append(f"{t0}–{t1} → Gare Centrale ({len(trains)} trains)")
    if not flights and not trains:
        hints: list[str] = []
        if next_flight:
            hints.append(f"first flight at {next_flight.effective_time.strftime('%H:%M')}")
        if next_train:
            hints.append(f"first train at {next_train.effective_time.strftime('%H:%M')}")
        if hints:
            recs.append(f"No arrivals now — {', '.join(hints)}")
        else:
            recs.append("No arrivals scheduled — rest or reposition")
    return recs


def _recs_tomorrow(
    flights: list[Arrival],
    trains: list[Arrival],
    blocks: list[TimeBlock],
) -> list[str]:
    recs: list[str] = []
    busiest = max(blocks, key=lambda b: b.count, default=None)
    if busiest and busiest.count > 0:
        recs.append(f"Busiest period: {busiest.label} ({busiest.count} arrivals)")
    if flights:
        recs.append(f"Morning flights: {len(flights)} — cover Airport early")
    if trains:
        recs.append(f"Trains all day: {len(trains)} scheduled at Gare Centrale")
    morning = next((b for b in blocks if b.start_hour == 8), None)
    evening = next((b for b in blocks if b.start_hour == 17), None)
    if morning and evening and morning.count and evening.count:
        if morning.count >= evening.count:
            recs.append("Shift tip: prioritize morning (08–12)")
        else:
            recs.append("Shift tip: prioritize evening (17–21)")
    if not recs:
        recs.append("No schedule data for tomorrow")
    return recs
