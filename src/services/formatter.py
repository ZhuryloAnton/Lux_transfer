from __future__ import annotations

from datetime import datetime

from src.models import Arrival, DemandPeak, Event, Report, SourceStatus, TimeBlock

NO_DATA = "⚠️ No real-time data available."


def format_now_report(r: Report) -> str:
    if _all_down(r):
        return (
            f"📊 <b>Next 3 Hours</b>\n"
            f"🕐 {r.generated_at.strftime('%A %d %b %Y, %H:%M')}\n\n"
            f"{NO_DATA}"
        )
    lines = [
        f"📊 <b>Next 3 Hours</b>",
        f"🕐 {r.generated_at.strftime('%A %d %b %Y, %H:%M')}",
        f"📅 {r.window_start.strftime('%H:%M')} – {r.window_end.strftime('%H:%M')}",
        "",
        _fmt_flights(r.flights, r.flights_status, r.flight_peaks, r.next_flight),
        _fmt_trains(r.trains, r.trains_status, r.train_peaks, r.next_train),
        _fmt_recs(r.recommendations),
    ]
    return "\n".join(lines)


def format_tomorrow_report(r: Report) -> str:
    if _all_down(r):
        return (
            f"📅 <b>Tomorrow</b>\n"
            f"🕐 Generated {r.generated_at.strftime('%H:%M')}\n\n"
            f"{NO_DATA}"
        )
    day = r.window_start.strftime("%A %d %b %Y")
    lines = [
        f"📅 <b>Tomorrow — {day}</b>",
        f"🕐 Generated {r.generated_at.strftime('%H:%M')}",
        "",
    ]
    if r.flights_status == SourceStatus.UNAVAILABLE:
        lines.append("✈️ <b>Morning Flights:</b> ⚠️ data unavailable\n")
    elif not r.flights:
        lines.append("✈️ <b>Morning Flights:</b> none scheduled\n")
    else:
        lines.append(f"✈️ <b>Morning Flights:</b> {len(r.flights)} arrivals")
        for a in r.flights[:10]:
            lines.append(f"  {a.effective_time.strftime('%H:%M')} — {a.identifier} from {a.origin}")
        if len(r.flights) > 10:
            lines.append(f"  <i>… +{len(r.flights) - 10} more</i>")
        if r.flight_peaks:
            p = r.flight_peaks[0]
            lines.append(f"  📈 Peak: {p.time_slot} ({p.count} flights)")
        lines.append("")

    if r.trains_status == SourceStatus.UNAVAILABLE:
        lines.append("🚆 <b>Trains:</b> ⚠️ data unavailable\n")
    elif not r.trains:
        lines.append("🚆 <b>Trains:</b> none scheduled\n")
    else:
        first = r.trains[0].effective_time.strftime("%H:%M")
        last = r.trains[-1].effective_time.strftime("%H:%M")
        pk = f" | Peak: {r.train_peaks[0].time_slot}" if r.train_peaks else ""
        lines.append(f"🚆 <b>Trains:</b> {len(r.trains)} arrivals ({first}–{last}){pk}\n")

    if r.time_blocks:
        lines.append(_fmt_blocks(r.time_blocks))

    lines.append(_fmt_recs(r.recommendations))
    return "\n".join(lines)


def _fmt_flights(
    arrivals: list[Arrival],
    status: SourceStatus,
    peaks: list[DemandPeak],
    next_arrival: Arrival | None = None,
) -> str:
    if status == SourceStatus.UNAVAILABLE:
        return "✈️ <b>Flights:</b>\n  ⚠️ Real-time data unavailable\n"
    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return f"✈️ <b>Flights:</b>\n  No flights in the next 3h\n  Next: {t} — {next_arrival.identifier} from {next_arrival.origin}\n"
        return "✈️ <b>Flights:</b>\n  No upcoming flights scheduled\n"
    lines = [f"✈️ <b>Flights:</b> ({len(arrivals)} arrivals)"]
    for a in arrivals[:8]:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(f"  {a.effective_time.strftime('%H:%M')} — {a.identifier} from {a.origin}{delay}")
    if len(arrivals) > 8:
        lines.append(f"  <i>… +{len(arrivals) - 8} more</i>")
    if peaks:
        lines.append(f"  📈 Peak: {peaks[0].time_slot} ({peaks[0].count} flights)")
    lines.append("")
    return "\n".join(lines)


def _fmt_trains(
    arrivals: list[Arrival],
    status: SourceStatus,
    peaks: list[DemandPeak],
    next_arrival: Arrival | None = None,
) -> str:
    if status == SourceStatus.UNAVAILABLE:
        return "🚆 <b>Trains:</b>\n  ⚠️ Real-time data unavailable\n"
    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return f"🚆 <b>Trains:</b>\n  No trains in the next 3h\n  Next: {t} — {next_arrival.identifier} from {next_arrival.origin}\n"
        return "🚆 <b>Trains:</b>\n  No upcoming trains scheduled\n"
    lines = [f"🚆 <b>Trains:</b> ({len(arrivals)} arrivals)"]
    for a in arrivals[:8]:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(f"  {a.effective_time.strftime('%H:%M')} — {a.identifier} from {a.origin}{delay}")
    if len(arrivals) > 8:
        lines.append(f"  <i>… +{len(arrivals) - 8} more</i>")
    if peaks:
        lines.append(f"  📈 Peak: {peaks[0].time_slot} ({peaks[0].count} trains)")
    lines.append("")
    return "\n".join(lines)


def _fmt_blocks(blocks: list[TimeBlock]) -> str:
    lines = ["📊 <b>By Time Block:</b>"]
    for b in blocks:
        if b.count == 0:
            lines.append(f"  ▫ {b.label}: —")
        else:
            fl = sum(1 for a in b.arrivals if a.transport_type.value == "flight")
            tr = sum(1 for a in b.arrivals if a.transport_type.value == "train")
            parts = []
            if fl:
                parts.append(f"{fl} ✈️")
            if tr:
                parts.append(f"{tr} 🚆")
            lines.append(f"  ▸ {b.label}: {b.count} ({', '.join(parts)})")
    lines.append("")
    return "\n".join(lines)


def _fmt_recs(recs: list[str]) -> str:
    if not recs:
        return "🚖 <b>Recommendation:</b>\n  Standard positioning"
    lines = ["🚖 <b>Recommendation:</b>"]
    for r in recs:
        lines.append(f"  ▸ {r}")
    return "\n".join(lines)


def format_events_report(events: list[Event], now: datetime) -> str:
    if not events:
        return (
            f"🎤 <b>Big Events — Luxembourg</b>\n"
            f"🕐 {now.strftime('%A %d %b %Y, %H:%M')}\n\n"
            f"No major events scheduled."
        )
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_events = [e for e in events if e.date.date() == today.date()]
    tomorrow_events = [e for e in events if e.date.date() != today.date()]

    lines = [
        f"🎤 <b>Big Events — Luxembourg</b>",
        f"🕐 {now.strftime('%A %d %b %Y, %H:%M')}",
        "",
    ]

    if today_events:
        lines.append(_fmt_day_events("Today", today_events))

    if tomorrow_events:
        day_label = tomorrow_events[0].date.strftime("%A %d %b")
        lines.append(_fmt_day_events(f"Tomorrow — {day_label}", tomorrow_events))

    notable = [e for e in events if e.demand_impact in ("high", "medium")]
    if notable:
        lines.append("🚖 <b>Taxi Recommendations:</b>")
        for e in notable:
            day = "today" if e.date.date() == today.date() else "tomorrow"
            if e.start_time:
                try:
                    h, m = map(int, e.start_time.split(":"))
                    end_hint = f"{h + 2}:{m:02d}" if h + 2 < 24 else f"{h + 2 - 24}:{m:02d}"
                    lines.append(f"  ▸ {e.venue} ~{end_hint} ({day}) — {e.name}")
                except ValueError:
                    lines.append(f"  ▸ {e.venue} ({day}) — {e.name}")
            else:
                lines.append(f"  ▸ {e.venue} ({day}) — {e.name}")
    else:
        lines.append("🚖 <b>Taxi Recommendations:</b>")
        lines.append("  ▸ No high-impact events — standard positioning")

    return "\n".join(lines)


def _fmt_day_events(label: str, events: list[Event]) -> str:
    notable = [e for e in events if e.demand_impact in ("high", "medium")]
    minor = [e for e in events if e.demand_impact == "low"]
    lines = [f"📅 <b>{label}</b> ({len(events)} events)\n"]
    for e in notable:
        lines.append(_fmt_event(e))
    if minor:
        if notable:
            lines.append("")
        lines.append(f"  <i>+ {len(minor)} smaller events:</i>")
        for e in minor[:5]:
            lines.append(f"  🟢 {e.name} — {e.venue or 'Luxembourg'}")
        if len(minor) > 5:
            lines.append(f"  <i>… +{len(minor) - 5} more</i>")
    lines.append("")
    return "\n".join(lines)


def _fmt_event(e: Event) -> str:
    parts = [f"\n{e.impact_emoji} <b>{e.name}</b>"]
    parts.append(f"  📍 {e.venue}" if e.venue else "  📍 Luxembourg City")
    parts.append(f"  📅 {e.date.strftime('%d %B %Y')}")
    if e.start_time:
        parts.append(f"  🕒 Start: {e.start_time}")
    else:
        parts.append(f"  🕒 Start: not specified")
    if e.end_time:
        parts.append(f"  🕔 End: {e.end_time}")
    else:
        parts.append(f"  🕔 End: not specified")
    parts.append(f"  🚖 Expected demand: {e.demand_impact.title()}")
    return "\n".join(parts)


def _all_down(r: Report) -> bool:
    return r.flights_status == SourceStatus.UNAVAILABLE and r.trains_status == SourceStatus.UNAVAILABLE
