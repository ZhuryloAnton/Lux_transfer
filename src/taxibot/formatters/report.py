"""Format Report objects into Telegram HTML strings."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from taxibot.core.text import escape
from taxibot.models import Arrival, DemandPeak, Report, SourceStatus, TimeBlock

_NO_DATA = "⚠️ Data temporarily unavailable."


def format_now_report(r: Report) -> str:
    ts = r.generated_at.strftime("%A %d %b %Y, %H:%M")
    win = f"{r.window_start.strftime('%H:%M')} – {r.window_end.strftime('%H:%M')}"
    if _both_down(r):
        return f"📊 <b>Next 3 Hours</b>\n🕐 {ts}\n\n{_NO_DATA}"
    parts = [
        f"📊 <b>Next 3 Hours</b>",
        f"🕐 {ts}   📅 {win}",
        "",
        _section_flights_now(r.flights, r.flights_status, r.flight_peaks, r.next_flight),
        _section_trains_now(r.trains, r.trains_status, r.train_peaks, r.next_train),
        _line_next_tgv(r.next_tgv),
        _section_recs(r.recommendations),
    ]
    return "\n".join(parts)


def format_trains_next_3h(r: Report) -> str:
    """Trains only for the next 3 hours (same format as in Next 3 Hours, no button)."""
    ts = r.generated_at.strftime("%A %d %b %Y, %H:%M")
    win = f"{r.window_start.strftime('%H:%M')} – {r.window_end.strftime('%H:%M')}"
    if r.trains_status == SourceStatus.UNAVAILABLE:
        return f"🚆 <b>Trains — Next 3 Hours</b>\n🕐 {ts}   📅 {win}\n\n  ⚠️ Data unavailable"
    parts = [
        "🚆 <b>Trains — Next 3 Hours</b>",
        f"🕐 {ts}   📅 {win}",
        "",
        _section_trains_now(r.trains, r.trains_status, r.train_peaks, r.next_train),
    ]
    return "\n".join(parts)


def format_fullday_report(r: Report, title: str) -> str:
    day = r.window_start.strftime("%A %d %b %Y")
    ts = r.generated_at.strftime("%H:%M")
    if _both_down(r):
        return f"{title} <b>{day}</b>\n🕐 Generated {ts}\n\n{_NO_DATA}"
    parts = [
        f"{title} <b>{day}</b>",
        f"🕐 Generated {ts}",
        "",
        _section_detailed_list(
            r.flights, r.flights_status,
            "✈️ <b>Flights (Luxembourg-Findel)</b>",
        ),
    ]
    if r.time_blocks:
        parts.append(_section_trains_by_block(r.trains, r.trains_status, r.time_blocks))
    else:
        parts.append(_section_detailed_list(
            r.trains, r.trains_status,
            "🚆 <b>Trains (Gare Centrale)</b>",
        ))
    if r.time_blocks:
        parts.append(_section_time_blocks(r.time_blocks))
    parts.append(_section_recs(r.recommendations))
    return "\n".join(parts)


def format_today_report(r: Report) -> str:
    return format_fullday_report(r, "📋 Today —")


def format_tomorrow_report(r: Report) -> str:
    day = r.window_start.strftime("%A %d %b %Y")
    ts = r.generated_at.strftime("%H:%M")
    if _both_down(r):
        return f"📅 Tomorrow — <b>{day}</b>\n🕐 Generated {ts}\n\n{_NO_DATA}"
    parts = [
        f"📅 Tomorrow — <b>{day}</b>",
        f"🕐 Generated {ts}",
        "",
    ]
    parts.append(_section_detailed_list(
        r.flights, r.flights_status,
        "✈️ <b>Flights (Luxembourg-Findel)</b>",
    ))
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


def _date_label(dt: datetime) -> str:
    """Label for when the train runs: Today, Tomorrow, or weekday + date."""
    tz = dt.tzinfo or pytz.timezone("Europe/Luxembourg")
    now = datetime.now(tz=tz)
    today = now.date()
    tomorrow = (now + timedelta(days=1)).date()
    d = dt.date()
    if d == today:
        return "Today"
    if d == tomorrow:
        return "Tomorrow"
    return dt.strftime("%a %d %b")


def _line_next_train(next_train: Arrival | None) -> str:
    """Single line: Next train at Gare Centrale (any type), with date when it runs."""
    if next_train is None:
        return ""
    when = _date_label(next_train.effective_time)
    t = next_train.effective_time.strftime("%H:%M")
    delay = f" ⏱+{next_train.delay_minutes}m" if next_train.delay_minutes else ""
    return f"🚆 <b>Next train:</b> {when} {t} — {escape(next_train.identifier)} from {escape(next_train.origin)}{delay}"


def format_next_train_report(next_train: Arrival | None) -> str:
    """Single message: next train (any type) whenever it is — today, tomorrow or later."""
    if next_train is None:
        return (
            "🚆 <b>Next train — Gare Centrale</b>\n\n"
            "No train found in the timetable (today or tomorrow).\n"
            "Check that GTFS_URL points to a feed that covers the current dates."
        )
    when = _date_label(next_train.effective_time)
    t = next_train.effective_time.strftime("%H:%M")
    delay = f" ⏱+{next_train.delay_minutes}m" if next_train.delay_minutes else ""
    return (
        f"🚆 <b>Next train — Gare Centrale</b>\n\n"
        f"<b>{when}</b> {t} — {escape(next_train.identifier)} from {escape(next_train.origin)}{delay}"
    )


def _format_tgv_line(tgv: Arrival) -> str:
    """Single TGV line: date and time, e.g. 5 March 2026 Paris 12:39 → Luxembourg 14:51."""
    lux_time = tgv.effective_time.strftime("%H:%M")
    day_num = tgv.effective_time.day
    month_name = tgv.effective_time.strftime("%B")
    year = tgv.effective_time.year
    date_str = f"{day_num} {month_name} {year}"
    if tgv.paris_departure:
        paris_time = tgv.paris_departure.strftime("%H:%M")
        return f"{date_str} Paris {paris_time} → Luxembourg {lux_time}"
    return f"{date_str} {lux_time} Paris → Luxembourg"


def _format_next_tgv_line(tgv: Arrival) -> str:
    """Next TGV line (same format as TGV today list)."""
    return f"🚄 <b>Next TGV:</b> {_format_tgv_line(tgv)}"


def _line_next_tgv(next_tgv: Arrival | None) -> str:
    """Single line: Next TGV Paris → Luxembourg with exact date."""
    if next_tgv is None:
        return ""
    return _format_next_tgv_line(next_tgv)


def format_tgv_schedule(tgvs: list[Arrival], day_label: str = "today") -> str:
    """Full daily TGV schedule (Paris → Luxembourg)."""
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    ts = now.strftime("%A %d %b %Y, %H:%M")
    header = "🚄 <b>TGV today — Paris → Luxembourg (Gare Centrale)</b>"
    if not tgvs:
        return f"{header}\n🕐 {ts}\n\n  No TGV in schedule today."
    lines = [
        header,
        f"🕐 {ts}   ({len(tgvs)} TGV)",
        "",
    ]
    for a in tgvs:
        lines.append(f"  {_format_tgv_line(a)}")
    return "\n".join(lines)


def format_next_tgv(tgv: Arrival | None) -> str:
    if tgv is None:
        return (
            "🚄 <b>Next TGV Paris → Luxembourg</b>\n\n"
            "No TGV found. This can mean no TGVs left today, or train data could not be loaded."
        )
    return "\n\n" + _format_next_tgv_line(tgv)


def _section_flights_now(
    arrivals: list[Arrival],
    status: SourceStatus,
    peaks: list[DemandPeak],
    next_arrival: Arrival | None,
) -> str:
    header = "✈️ <b>Flights (Luxembourg-Findel International Airport)</b>"
    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return (
                f"{header}\n  Nothing in the next 3h\n"
                f"  Next: {t} — {escape(next_arrival.identifier)} from {escape(next_arrival.origin)}\n"
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
    arrivals: list[Arrival],
    status: SourceStatus,
    peaks: list[DemandPeak],
    next_arrival: Arrival | None,
) -> str:
    header = "🚆 <b>Trains (Gare Centrale)</b>"
    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return (
                f"{header}\n  Nothing in the next 3h\n"
                f"  Next: {t} — {escape(next_arrival.identifier)} from {escape(next_arrival.origin)}\n"
            )
        return f"{header}\n  No upcoming trains\n"
    lines = [f"{header} ({len(arrivals)})"]
    for a in arrivals:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        line = (
            f"  {a.effective_time.strftime('%H:%M')} "
            f"{escape(a.identifier)} ← {escape(a.origin)}{delay}"
        )
        if "TGV" in (a.identifier or "").upper():
            line = f"  <b>{a.effective_time.strftime('%H:%M')} {escape(a.identifier)} ← {escape(a.origin)}{delay}</b>"
        lines.append(line)
    if peaks:
        lines.append(f"  📈 Peak slot: {peaks[0].time_slot} ({peaks[0].count} trains)")
    lines.append("")
    return "\n".join(lines)


def _section_detailed_list(
    arrivals: list[Arrival],
    status: SourceStatus,
    header: str,
) -> str:
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
                p for p in [f"{fl} ✈️" if fl else "", f"{tr} 🚆" if tr else ""] if p
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


def _both_down(r: Report) -> bool:
    return (
        r.flights_status == SourceStatus.UNAVAILABLE
        and r.trains_status == SourceStatus.UNAVAILABLE
    )
