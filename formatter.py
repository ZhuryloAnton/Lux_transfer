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


def format_fullday_report(r: Report, title: str, *, full_schedule: bool = False) -> str:
    day = r.window_start.strftime("%A %d %b %Y")
    ts  = r.generated_at.strftime("%H:%M")

    if _both_down(r):
        return f"{title} <b>{day}</b>\n🕐 Generated {ts}\n\n{_NO_DATA}"

    if full_schedule:
        parts = [
            f"{title} <b>{day}</b>",
            f"🕐 Generated {ts}",
            "",
            _section_flights_fullday_short(r.flights, r.flights_status),
            _section_trains_fullday_short(r.trains, r.trains_status),
            _section_recs(r.recommendations),
        ]
    else:
        parts = [
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
    return format_fullday_report(r, "📋 Today —", full_schedule=True)


def format_next_tgv(tgv: Arrival | None) -> str:
    """Single line: next TGV (used at bottom of Now/Today reports)."""
    if tgv is None:
        return "\n\n🚄 <b>Next TGV:</b> none found in schedule"
    t = tgv.effective_time.strftime("%H:%M")
    d = tgv.effective_time.strftime("%a %d %b")
    return (
        f"\n\n🚄 <b>Next TGV → Gare Centrale:</b> "
        f"{t} ({d}) from {escape(tgv.origin)}"
    )


def format_today_tgv(tgvs: list[Arrival]) -> str:
    """Full list of today's TGVs (Paris → Gare Centrale), short format."""
    from datetime import datetime
    import pytz
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    ts = now.strftime("%A %d %b %Y, %H:%M")
    header = "🚄 <b>TGV today — Paris → Gare Centrale</b>"
    if not tgvs:
        return f"{header}\n🕐 {ts}\n\n  No TGV in schedule today."
    lines = [header, f"🕐 {ts}   ({len(tgvs)} TGV)", ""]
    for a in tgvs:
        delay = f" +{a.delay_minutes}m" if a.delay_minutes else ""
        gare_time = a.effective_time.strftime("%H:%M")
        if a.origin and "Paris" in a.origin and a.paris_departure:
            paris_time = a.paris_departure.strftime("%H:%M")
            lines.append(f"  {gare_time}  (Paris({paris_time}) → Gare({gare_time})){delay}")
        else:
            lines.append(f"  {gare_time}  from {escape(a.origin)}{delay}")
    return "\n".join(lines)


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
        gare_time = a.effective_time.strftime("%H:%M")
        is_tgv_from_paris = (
            a.identifier.upper() == "TGV"
            and a.origin and "Paris" in a.origin
        )
        if is_tgv_from_paris:
            paris_time = (
                a.paris_departure.strftime("%H:%M")
                if a.paris_departure else "?"
            )
            lines.append(
                f"  {gare_time} {escape(a.identifier)} "
                f"(Paris({paris_time}) → Gare Central({gare_time})){delay}"
            )
        else:
            lines.append(
                f"  {gare_time} "
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


def _section_flights_fullday_short(
    arrivals: list[Arrival],
    status:   SourceStatus,
) -> str:
    """Full list of flights for today, one short line each."""
    header = "✈️ <b>Flights (Findel)</b>"
    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    if not arrivals:
        return f"{header}\n  None scheduled\n"
    lines = [f"{header} ({len(arrivals)})"]
    for a in arrivals:
        delay = f" +{a.delay_minutes}m" if a.delay_minutes else ""
        lines.append(
            f"  {a.effective_time.strftime('%H:%M')} "
            f"{escape(a.identifier)} ← {escape(a.origin)}{delay}"
        )
    lines.append("")
    return "\n".join(lines)


def _section_trains_fullday_short(
    arrivals: list[Arrival],
    status:   SourceStatus,
) -> str:
    """Trains for today: TGV/IC/ICE listed individually, regional trains summarised."""
    header = "🚆 <b>Trains (Gare Centrale)</b>"
    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    if not arrivals:
        return f"{header}\n  None scheduled\n"

    # Split into high-demand (listed) and regional (summarised)
    highlight = {"TGV", "IC", "ICE", "EC"}
    important = [a for a in arrivals if a.identifier.upper() in highlight]
    regional  = [a for a in arrivals if a.identifier.upper() not in highlight]

    lines = [f"{header} ({len(arrivals)})"]

    # List TGV / IC / ICE individually
    if important:
        for a in important:
            delay = f" ⏱+{a.delay_minutes}m" if a.delay_minutes else ""
            gare_time = a.effective_time.strftime("%H:%M")
            is_tgv_from_paris = (
                a.identifier.upper() == "TGV"
                and a.origin and "Paris" in a.origin
            )
            if is_tgv_from_paris:
                paris_time = a.paris_departure.strftime("%H:%M") if a.paris_departure else "?"
                lines.append(
                    f"  {gare_time} {escape(a.identifier)} "
                    f"(Paris({paris_time}) → Gare({gare_time})){delay}"
                )
            else:
                lines.append(
                    f"  {gare_time} {escape(a.identifier)} ← {escape(a.origin)}{delay}"
                )

    # Summarise regional trains by type
    if regional:
        from collections import Counter
        by_type: Counter[str] = Counter(a.identifier for a in regional)
        first = regional[0].effective_time.strftime("%H:%M")
        last  = regional[-1].effective_time.strftime("%H:%M")
        type_parts = [f"{c} {t}" for t, c in by_type.most_common()]
        lines.append(f"  <i>Regional ({first}–{last}): {', '.join(type_parts)}</i>")

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
