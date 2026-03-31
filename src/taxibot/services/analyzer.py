"""Build Report objects from raw Arrival lists.

Pure data logic — no I/O, no framework dependencies.
  build_now_report()    → 3-hour window report
  build_fullday_report() → full-day report (today).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

import pytz

from taxibot.models import Arrival, DemandPeak, Report, SourceStatus, TimeBlock

_LUX_TZ = pytz.timezone("Europe/Luxembourg")

_TIME_BLOCKS = [
    ("Early Morning (05–08)", 5,  8),
    ("Morning      (08–12)", 8,  12),
    ("Afternoon    (12–17)", 12, 17),
    ("Evening      (17–21)", 17, 21),
    ("Night        (21–00)", 21, 24),
]


# ── Public builders ───────────────────────────────────────────────────────────

def build_now_report(
    flights: list[Arrival],
    trains:  list[Arrival],
    *,
    flights_ok: bool,
    trains_ok:  bool,
) -> Report:
    now        = datetime.now(tz=_LUX_TZ)
    window_end = now + timedelta(hours=3)

    f = _in_window(flights, now, window_end)
    t = _in_window(trains,  now, window_end)

    active_flights = [fl for fl in flights if not fl.is_cancelled]
    next_flight = _first_after(active_flights, now) if not f and flights_ok else None
    next_train  = _first_after(trains,  now) if not t and trains_ok  else None

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
        trains_status=SourceStatus.OK if trains_ok  else SourceStatus.UNAVAILABLE,
        next_flight=next_flight,
        next_train=next_train,
    )


def build_fullday_report(
    flights: list[Arrival],
    trains:  list[Arrival],
    *,
    flights_ok: bool,
    trains_ok:  bool,
    day: datetime,
) -> Report:
    """Full-day report — used for Today schedule."""
    now   = datetime.now(tz=_LUX_TZ)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = day.replace(hour=23, minute=59, second=59)

    combined = sorted(flights + trains, key=lambda a: a.effective_time)
    blocks   = _time_blocks(combined, start)

    return Report(
        generated_at=now,
        window_start=start,
        window_end=end,
        flights=sorted(flights, key=lambda a: a.effective_time),
        trains=sorted(trains,  key=lambda a: a.effective_time),
        flight_peaks=_peaks(flights, "Airport"),
        train_peaks=_peaks(trains,  "Gare Centrale"),
        recommendations=_recs_fullday(flights, trains, blocks),
        flights_status=SourceStatus.OK if flights_ok else SourceStatus.UNAVAILABLE,
        trains_status=SourceStatus.OK if trains_ok  else SourceStatus.UNAVAILABLE,
        time_blocks=blocks,
    )


def build_tomorrow_report(
    flights: list[Arrival],
    trains:  list[Arrival],
    *,
    flights_ok: bool,
    trains_ok:  bool,
) -> Report:
    """Full-day report for tomorrow."""
    now = datetime.now(tz=_LUX_TZ)
    tomorrow = now + timedelta(days=1)
    start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    end = tomorrow.replace(hour=23, minute=59, second=59)

    combined = sorted(flights + trains, key=lambda a: a.effective_time)
    blocks = _time_blocks(combined, start)

    return Report(
        generated_at=now,
        window_start=start,
        window_end=end,
        flights=sorted(flights, key=lambda a: a.effective_time),
        trains=sorted(trains, key=lambda a: a.effective_time),
        flight_peaks=_peaks(flights, "Airport"),
        train_peaks=_peaks(trains, "Gare Centrale"),
        recommendations=_recs_fullday(flights, trains, blocks),
        flights_status=SourceStatus.OK if flights_ok else SourceStatus.UNAVAILABLE,
        trains_status=SourceStatus.OK if trains_ok else SourceStatus.UNAVAILABLE,
        time_blocks=blocks,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _in_window(arrivals: list[Arrival], start: datetime, end: datetime) -> list[Arrival]:
    return sorted(
        (a for a in arrivals if start <= a.effective_time <= end),
        key=lambda a: a.effective_time,
    )


def _first_after(arrivals: list[Arrival], after: datetime) -> Arrival | None:
    future = [a for a in arrivals if a.effective_time > after]
    return min(future, key=lambda a: a.effective_time) if future else None


def _peaks(arrivals: list[Arrival], location: str) -> list[DemandPeak]:
    active = [a for a in arrivals if not a.is_cancelled]
    if not active:
        return []
    slots: Counter[str] = Counter()
    for a in active:
        half = "00" if a.effective_time.minute < 30 else "30"
        slots[f"{a.effective_time.strftime('%H:')}{ half}"] += 1
    return [
        DemandPeak(time_slot=s, count=c, location=location)
        for s, c in slots.most_common(3)
        if c >= 2
    ]


def _time_blocks(arrivals: list[Arrival], day: datetime) -> list[TimeBlock]:
    blocks: list[TimeBlock] = []
    for label, sh, eh in _TIME_BLOCKS:
        b_start = day.replace(hour=sh, minute=0,  second=0)
        b_end   = day.replace(hour=min(eh, 23), minute=59, second=59)
        items   = [a for a in arrivals if b_start <= a.effective_time <= b_end]
        blocks.append(TimeBlock(label=label, start_hour=sh, end_hour=eh, arrivals=items))
    return blocks


def _recs_now(
    flights:    list[Arrival],
    trains:     list[Arrival],
    next_flight: Arrival | None,
    next_train:  Arrival | None,
) -> list[str]:
    recs: list[str] = []

    active_fl = [a for a in flights if not a.is_cancelled]
    if active_fl:
        f0 = active_fl[0].effective_time.strftime("%H:%M")
        f1 = active_fl[-1].effective_time.strftime("%H:%M")
        recs.append(f"{f0}–{f1} → Airport ({len(active_fl)} flight{'s' if len(active_fl)!=1 else ''})")

    tgvs = [t for t in trains if t.identifier.upper() == "TGV"]
    if tgvs:
        t0 = tgvs[0].effective_time.strftime("%H:%M")
        t1 = tgvs[-1].effective_time.strftime("%H:%M")
        recs.append(f"{t0}–{t1} → Gare Centrale ({len(tgvs)} TGV)")

    if not active_fl and not tgvs:
        hints: list[str] = []
        if next_flight:
            hints.append(f"first flight at {next_flight.effective_time.strftime('%H:%M')}")
        if next_train:
            hints.append(f"first train at {next_train.effective_time.strftime('%H:%M')}")
        if hints:
            recs.append(f"Quiet window — {', '.join(hints)}")
        else:
            recs.append("No arrivals scheduled — rest or reposition")

    return recs


def _recs_fullday(
    flights: list[Arrival],
    trains:  list[Arrival],
    blocks:  list[TimeBlock],
) -> list[str]:
    recs: list[str] = []

    if blocks:
        def _relevant_count(b: TimeBlock) -> int:
            return sum(1 for a in b.arrivals
                       if (a.transport_type.value == "flight" and not a.is_cancelled)
                       or (a.transport_type.value == "train" and a.identifier.upper() == "TGV"))
        busiest = max(blocks, key=_relevant_count)
        bc = _relevant_count(busiest)
        if bc > 0:
            recs.append(f"Busiest block: {busiest.label} ({bc} arrival{'s' if bc != 1 else ''})")

    active_fl = [a for a in flights if not a.is_cancelled]
    if active_fl:
        recs.append(f"Airport: {len(active_fl)} flight{'s' if len(active_fl)!=1 else ''}")
    tgvs = [t for t in trains if t.identifier.upper() == "TGV"]
    if tgvs:
        recs.append(f"Gare Centrale: {len(tgvs)} TGV")

    morning = next((b for b in blocks if b.start_hour == 8),  None)
    evening = next((b for b in blocks if b.start_hour == 17), None)
    if morning and evening and morning.count and evening.count:
        tip = "morning (08–12)" if morning.count >= evening.count else "evening (17–21)"
        recs.append(f"Best shift: {tip}")

    if not recs:
        recs.append("No schedule data — check again later")

    return recs
