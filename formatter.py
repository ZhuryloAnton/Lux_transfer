"""Format Report objects into Telegram HTML strings.

All dynamic values are HTML-escaped before insertion.
No business logic — presentation only.
"""

from __future__ import annotations

from models import Arrival, DemandPeak, Report, SourceStatus, TimeBlock
from text import escape

_NO_DATA = "⚠️ Data temporarily unavailable."


# ── Public formatters ─────────────────────────────────────────────────────────

def format_now_report(r: Report) -> str:
    ts   = r.generated_at.strftime("%A %d %b %Y, %H:%M")
    win  = f"{r.window_start.strftime('%H:%M')} – {r.window_end.strftime('%H:%M')}"

    if _both_down(r):
        return f"📊 <b>Next 3 Hours</b>\n🕐 {ts}\n\n{_NO_DATA}"

    parts = [
        f"📊 <b>Next 3 Hours</b>",
        f"🕐 {ts}   📅 {win}",
        "",
        _section_flights_now(r.flights, r.flights_status, r.flight_peaks, r.next_flight),
        _section_trains_now(r.trains,   r.trains_status,  r.train_peaks,  r.next_train),
        _section_recs(r.recommendations),
    ]
    return "\n".join(parts)


def format_fullday_report(r: Report, title: str) -> str:
    day = r.window_start.strftime("%A %d %b %Y")
    ts  = r.generated_at.strftime("%H:%M")

    if _both_down(r):
        return f"{title} <b>{day}</b>\n🕐 Generated {ts}\n\n{_NO_DATA}"

    parts: list[str] = [
        f"{title} <b>{day}</b>",
        f"🕐 Generated {ts}",
        "",
        _section_flights_summary(r.flights, r.flights_status, r.flight_peaks),
        _section_trains_summary(r.trains, r.trains_status, r.train_peaks),
    ]

    if r.time_blocks:
        parts.append(_section_time_blocks(r.time_blocks))

    parts.append(_section_recs(r.recommendations))
    return "\n".join(parts)


def format_today_report(r: Report) -> str:
    return format_fullday_report(r, "📋 Today —")


def format_tomorrow_report(r: Report) -> str:
    day = r.window_start.strftime("%A %d %b %Y")
    ts  = r.generated_at.strftime("%H:%M")

    if _both_down(r):
        return f"📅 Tomorrow — <b>{day}</b>\n🕐 Generated {ts}\n\n{_NO_DATA}"

    parts: list[str] = [
        f"📅 Tomorrow — <b>{day}</b>",
        f"🕐 Generated {ts}",
        "",
    ]

    # Flights — detailed list
    parts.append(_section_detailed_list(
        r.flights, r.flights_status,
        "✈️ <b>Flights (Luxembourg-Findel)</b>",
    ))

    # Trains — grouped by time block
    if r.time_blocks:
        parts.append(_section_trains_by_block(r.trains, r.trains_status, r.time_blocks))
    else:
        parts.append(_section_detailed_list(
            r.trains, r.trains_status,
            "🚆 <b>Trains (Gare Centrale)</b>",
        ))

    parts.append(_section_recs(r.recommendations))
    return "\n".join(parts)


def format_flights_report(flights: list[Arrival], ok: bool) -> str:
    """Flights-only detailed report."""
    from datetime import datetime
    import pytz
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    ts = now.strftime("%A %d %b %Y, %H:%M")

    header = "✈️ <b>Flights — Luxembourg-Findel International Airport</b>"

    if not ok:
        return f"{header}\n🕐 {ts}\n\n  ⚠️ Data unavailable"

    if not flights:
        return f"{header}\n🕐 {ts}\n\n  No upcoming flights today"

    lines = [
        header,
        f"🕐 {ts}   ({len(flights)} arrival{'s' if len(flights)!=1 else ''})",
        "",
    ]
    for a in flights:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(
            f"  {a.effective_time.strftime('%H:%M')} "
            f"{escape(a.identifier)} ← {escape(a.origin)}{delay}"
        )
    return "\n".join(lines)


def format_next_tgv(tgv: Arrival | None) -> str:
    if tgv is None:
        return "\n\n🚄 <b>Next TGV Paris → Luxembourg:</b> none found in schedule"

    lux_time = tgv.effective_time.strftime("%H:%M")
    day = tgv.effective_time.strftime("%a %d %b")

    if tgv.paris_departure:
        paris_time = tgv.paris_departure.strftime("%H:%M")
        return (
            f"\n\n🚄 <b>Next TGV:</b> Paris ({paris_time}) → Luxembourg ({lux_time}) — {day}"
        )

    return (
        f"\n\n🚄 <b>Next TGV:</b> → Luxembourg ({lux_time}) — {day}"
    )


# ── Section builders ──────────────────────────────────────────────────────────

def _section_flights_now(
    arrivals:     list[Arrival],
    status:       SourceStatus,
    peaks:        list[DemandPeak],
    next_arrival: Arrival | None,
) -> str:
    header = "✈️ <b>Flights (Luxembourg-Findel International Airport)</b>"

    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"

    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return (
                f"{header}\n"
                f"  Nothing in the next 3h\n"
                f"  Next: {t} — {escape(next_arrival.identifier)} "
                f"from {escape(next_arrival.origin)}\n"
            )
        return f"{header}\n  No upcoming flights\n"

    lines = [f"{header} ({len(arrivals)})"]
    for a in arrivals[:8]:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(
            f"  {a.effective_time.strftime('%H:%M')} "
            f"{escape(a.identifier)} ← {escape(a.origin)}{delay}"
        )
    if len(arrivals) > 8:
        lines.append(f"  <i>+{len(arrivals) - 8} more…</i>")
    if peaks:
        lines.append(f"  📈 Peak slot: {peaks[0].time_slot} ({peaks[0].count} flights)")
    lines.append("")
    return "\n".join(lines)


def _section_trains_now(
    arrivals:     list[Arrival],
    status:       SourceStatus,
    peaks:        list[DemandPeak],
    next_arrival: Arrival | None,
) -> str:
    header = "🚆 <b>Trains (Gare Centrale)</b>"

    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"

    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return (
                f"{header}\n"
                f"  Nothing in the next 3h\n"
                f"  Next: {t} — {escape(next_arrival.identifier)} "
                f"from {escape(next_arrival.origin)}\n"
            )
        return f"{header}\n  No upcoming trains\n"

    lines = [f"{header} ({len(arrivals)})"]
    for a in arrivals[:8]:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(
            f"  {a.effective_time.strftime('%H:%M')} "
            f"{escape(a.identifier)} ← {escape(a.origin)}{delay}"
        )
    if len(arrivals) > 8:
        lines.append(f"  <i>+{len(arrivals) - 8} more…</i>")
    if peaks:
        lines.append(f"  📈 Peak slot: {peaks[0].time_slot} ({peaks[0].count} trains)")
    lines.append("")
    return "\n".join(lines)


def _section_flights_summary(
    arrivals: list[Arrival],
    status:   SourceStatus,
    peaks:    list[DemandPeak],
) -> str:
    header = "✈️ <b>Flights (Luxembourg-Findel)</b>"

    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    if not arrivals:
        return f"{header}\n  None scheduled\n"

    first = arrivals[0].effective_time.strftime("%H:%M")
    last  = arrivals[-1].effective_time.strftime("%H:%M")
    peak  = f" | Peak: {peaks[0].time_slot}" if peaks else ""
    return (
        f"{header}\n"
        f"  {len(arrivals)} arrivals  {first} – {last}{peak}\n"
    )


def _section_trains_summary(
    arrivals: list[Arrival],
    status:   SourceStatus,
    peaks:    list[DemandPeak],
) -> str:
    header = "🚆 <b>Trains (Gare Centrale)</b>"

    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    if not arrivals:
        return f"{header}\n  None scheduled\n"

    first = arrivals[0].effective_time.strftime("%H:%M")
    last  = arrivals[-1].effective_time.strftime("%H:%M")
    peak  = f" | Peak: {peaks[0].time_slot}" if peaks else ""
    return (
        f"{header}\n"
        f"  {len(arrivals)} arrivals  {first} – {last}{peak}\n"
    )


def _section_detailed_list(
    arrivals: list[Arrival],
    status:   SourceStatus,
    header:   str,
) -> str:
    """Full listing of arrivals — used for tomorrow's detailed view."""
    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    if not arrivals:
        return f"{header}\n  None scheduled\n"

    lines = [f"{header} — {len(arrivals)} arrival{'s' if len(arrivals)!=1 else ''}"]
    for a in arrivals:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(
            f"  {a.effective_time.strftime('%H:%M')} "
            f"{escape(a.identifier)} ← {escape(a.origin)}{delay}"
        )
    lines.append("")
    return "\n".join(lines)


def _section_trains_by_block(
    trains: list[Arrival],
    status: SourceStatus,
    blocks: list[TimeBlock],
) -> str:
    """Trains grouped by time period — detailed view for tomorrow."""
    header = "🚆 <b>Trains (Gare Centrale)</b>"

    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"

    total = len(trains)
    if total == 0:
        return f"{header}\n  None scheduled\n"

    lines = [f"{header} — {total} arrival{'s' if total!=1 else ''}"]
    for b in blocks:
        block_trains = [
            a for a in trains
            if a.effective_time.hour >= b.start_hour
            and a.effective_time.hour < (b.end_hour if b.end_hour < 24 else 24)
        ]
        if not block_trains:
            continue
        block_trains.sort(key=lambda a: a.effective_time)
        lines.append(f"\n  <b>{b.label}</b> ({len(block_trains)})")
        for a in block_trains:
            lines.append(
                f"    {a.effective_time.strftime('%H:%M')} "
                f"{escape(a.identifier)} ← {escape(a.origin)}"
            )
    lines.append("")
    return "\n".join(lines)


def _section_time_blocks(blocks: list[TimeBlock]) -> str:
    lines = ["📊 <b>By Period</b>"]
    for b in blocks:
        if b.count == 0:
            lines.append(f"  ▫ {b.label}: quiet")
        else:
            fl = sum(1 for a in b.arrivals if a.transport_type.value == "flight")
            tr = sum(1 for a in b.arrivals if a.transport_type.value == "train")
            detail = "  ".join(
                p for p in [
                    f"{fl} ✈️" if fl else "",
                    f"{tr} 🚆" if tr else "",
                ] if p
            )
            lines.append(f"  ▸ {b.label}: {b.count} arrivals  ({detail})")
    lines.append("")
    return "\n".join(lines)


def _section_recs(recs: list[str]) -> str:
    if not recs:
        return "🚖 <b>Tip:</b> Standard positioning"
    lines = ["🚖 <b>Positioning Tips</b>"]
    for r in recs:
        lines.append(f"  ▸ {r}")
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _both_down(r: Report) -> bool:
    return (
        r.flights_status == SourceStatus.UNAVAILABLE
        and r.trains_status == SourceStatus.UNAVAILABLE
    )
