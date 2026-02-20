"""Format Report objects into Telegram HTML messages.

No event formatting. All user-visible strings are produced here.
Dynamic values are HTML-escaped before insertion.
"""

from __future__ import annotations

from bot.models import Arrival, DemandPeak, Report, SourceStatus, TimeBlock
from utils.text import escape

_NO_DATA = "⚠️ No real-time data available."


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------


def format_now_report(r: Report) -> str:
    if _all_sources_down(r):
        return (
            f"📊 <b>Next 3 Hours</b>\n"
            f"🕐 {r.generated_at.strftime('%A %d %b %Y, %H:%M')}\n\n"
            f"{_NO_DATA}"
        )
    lines = [
        "📊 <b>Next 3 Hours</b>",
        f"🕐 {r.generated_at.strftime('%A %d %b %Y, %H:%M')}",
        f"📅 {r.window_start.strftime('%H:%M')} – {r.window_end.strftime('%H:%M')}",
        "",
        _fmt_flights_now(r.flights, r.flights_status, r.flight_peaks, r.next_flight),
        _fmt_trains_now(r.trains, r.trains_status, r.train_peaks, r.next_train),
        _fmt_recs(r.recommendations),
    ]
    return "\n".join(lines)


def format_tomorrow_report(r: Report) -> str:
    if _all_sources_down(r):
        return (
            f"📅 <b>Tomorrow</b>\n"
            f"🕐 Generated {r.generated_at.strftime('%H:%M')}\n\n"
            f"{_NO_DATA}"
        )
    day = r.window_start.strftime("%A %d %b %Y")
    lines = [
        f"📅 <b>Tomorrow — {day}</b>",
        f"🕐 Generated {r.generated_at.strftime('%H:%M')}",
        "",
    ]

    # Flights section
    if r.flights_status == SourceStatus.UNAVAILABLE:
        lines.append("✈️ <b>Morning Flights:</b> ⚠️ data unavailable\n")
    elif not r.flights:
        lines.append("✈️ <b>Morning Flights:</b> none scheduled\n")
    else:
        lines.append(f"✈️ <b>Morning Flights:</b> {len(r.flights)} arrivals")
        for a in r.flights[:10]:
            delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
            lines.append(
                f"  {a.effective_time.strftime('%H:%M')} — "
                f"{escape(a.identifier)} from {escape(a.origin)}{delay}"
            )
        if len(r.flights) > 10:
            lines.append(f"  <i>… +{len(r.flights) - 10} more</i>")
        if r.flight_peaks:
            p = r.flight_peaks[0]
            lines.append(f"  📈 Peak: {p.time_slot} ({p.count} flights)")
        lines.append("")

    # Trains section
    if r.trains_status == SourceStatus.UNAVAILABLE:
        lines.append("🚆 <b>Trains (Gare Centrale):</b> ⚠️ data unavailable\n")
    elif not r.trains:
        lines.append("🚆 <b>Trains (Gare Centrale):</b> none scheduled\n")
    else:
        first = r.trains[0].effective_time.strftime("%H:%M")
        last = r.trains[-1].effective_time.strftime("%H:%M")
        peak = f" | Peak: {r.train_peaks[0].time_slot}" if r.train_peaks else ""
        lines.append(
            f"🚆 <b>Trains (Gare Centrale):</b> {len(r.trains)} arrivals "
            f"({first}–{last}){peak}\n"
        )

    if r.time_blocks:
        lines.append(_fmt_time_blocks(r.time_blocks))

    lines.append(_fmt_recs(r.recommendations))
    return "\n".join(lines)


def format_next_tgv(tgv: Arrival | None) -> str:
    if tgv is None:
        return "\n\n🚄 <b>Next TGV:</b> no data available"
    t = tgv.effective_time.strftime("%H:%M")
    d = tgv.effective_time.strftime("%a %d %b")
    return f"\n\n🚄 <b>Next TGV to Gare Centrale:</b> {t} ({d}) from {escape(tgv.origin)}"


# ---------------------------------------------------------------------------
# Private section formatters
# ---------------------------------------------------------------------------


def _fmt_flights_now(
    arrivals: list[Arrival],
    status: SourceStatus,
    peaks: list[DemandPeak],
    next_arrival: Arrival | None,
) -> str:
    if status == SourceStatus.UNAVAILABLE:
        return "✈️ <b>Flights:</b>\n  ⚠️ Real-time data unavailable\n"
    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return (
                f"✈️ <b>Flights:</b>\n"
                f"  No flights in the next 3h\n"
                f"  Next: {t} — {escape(next_arrival.identifier)} "
                f"from {escape(next_arrival.origin)}\n"
            )
        return "✈️ <b>Flights:</b>\n  No upcoming flights scheduled\n"

    lines = [f"✈️ <b>Flights:</b> ({len(arrivals)} arrivals)"]
    for a in arrivals[:8]:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(
            f"  {a.effective_time.strftime('%H:%M')} — "
            f"{escape(a.identifier)} from {escape(a.origin)}{delay}"
        )
    if len(arrivals) > 8:
        lines.append(f"  <i>… +{len(arrivals) - 8} more</i>")
    if peaks:
        lines.append(f"  📈 Peak: {peaks[0].time_slot} ({peaks[0].count} flights)")
    lines.append("")
    return "\n".join(lines)


def _fmt_trains_now(
    arrivals: list[Arrival],
    status: SourceStatus,
    peaks: list[DemandPeak],
    next_arrival: Arrival | None,
) -> str:
    if status == SourceStatus.UNAVAILABLE:
        return "🚆 <b>Trains (Gare Centrale):</b>\n  ⚠️ Real-time data unavailable\n"
    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return (
                f"🚆 <b>Trains (Gare Centrale):</b>\n"
                f"  No trains in the next 3h\n"
                f"  Next: {t} — {escape(next_arrival.identifier)} "
                f"from {escape(next_arrival.origin)}\n"
            )
        return "🚆 <b>Trains (Gare Centrale):</b>\n  No upcoming trains scheduled\n"

    lines = [f"🚆 <b>Trains (Gare Centrale):</b> ({len(arrivals)} arrivals)"]
    for a in arrivals[:8]:
        delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(
            f"  {a.effective_time.strftime('%H:%M')} — "
            f"{escape(a.identifier)} from {escape(a.origin)}{delay}"
        )
    if len(arrivals) > 8:
        lines.append(f"  <i>… +{len(arrivals) - 8} more</i>")
    if peaks:
        lines.append(f"  📈 Peak: {peaks[0].time_slot} ({peaks[0].count} trains)")
    lines.append("")
    return "\n".join(lines)


def _fmt_time_blocks(blocks: list[TimeBlock]) -> str:
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
    for rec in recs:
        lines.append(f"  ▸ {rec}")
    return "\n".join(lines)


def _all_sources_down(r: Report) -> bool:
    return (
        r.flights_status == SourceStatus.UNAVAILABLE
        and r.trains_status == SourceStatus.UNAVAILABLE
    )
