"""Format Report objects into Telegram HTML strings."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from taxibot.core.text import escape
from taxibot.models import Arrival, DemandPeak, Report, SourceStatus, TimeBlock

_NO_DATA = "⚠️ Data temporarily unavailable."
_SEP = "─────────────────────────"
_FLIGHTS_PER_PAGE = 5


def format_now_report(r: Report) -> str:
    ts = r.generated_at.strftime("%A %d %b %Y, %H:%M")
    win = f"{r.window_start.strftime('%H:%M')} – {r.window_end.strftime('%H:%M')}"
    if _both_down(r):
        return f"📊 <b>Next 3 Hours</b>\n🕐 {ts}\n\n{_NO_DATA}"
    parts = [
        f"📊 <b>Next 3 Hours</b>",
        f"🕐 {ts}",
        f"📅 {win}",
        "",
        _section_flights_now(r.flights, r.flights_status, r.flight_peaks, r.next_flight),
        _SEP,
        "",
        _section_trains_now(r.trains, r.trains_status, r.train_peaks, r.next_train),
        _line_next_tgv(r.next_tgv),
        "",
        _SEP,
        "",
        _section_recs(r.recommendations),
    ]
    return "\n".join(parts)


def format_trains_next_3h(r: Report) -> str:
    """TGV only for the next 3 hours."""
    ts = r.generated_at.strftime("%A %d %b %Y, %H:%M")
    win = f"{r.window_start.strftime('%H:%M')} – {r.window_end.strftime('%H:%M')}"
    if r.trains_status == SourceStatus.UNAVAILABLE:
        return f"🚄 <b>TGV — Next 3 Hours</b>\n🕐 {ts}\n📅 {win}\n\n  ⚠️ Data unavailable"
    parts = [
        "🚄 <b>TGV — Next 3 Hours</b>",
        f"🕐 {ts}",
        f"📅 {win}",
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
        _SEP,
        "",
    ]
    if r.time_blocks:
        parts.append(_section_trains_by_block(r.trains, r.trains_status, r.time_blocks))
    else:
        parts.append(_section_detailed_list(
            r.trains, r.trains_status,
            "🚄 <b>TGV (Gare Centrale)</b>",
        ))
    parts.append("")
    parts.append(_SEP)
    parts.append("")
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
    parts.append(_SEP)
    parts.append("")
    if r.time_blocks:
        parts.append(_section_trains_by_block(r.trains, r.trains_status, r.time_blocks))
    else:
        parts.append(_section_detailed_list(
            r.trains, r.trains_status,
            "🚄 <b>TGV (Gare Centrale)</b>",
        ))
    parts.append("")
    parts.append(_SEP)
    parts.append("")
    parts.append(_section_recs(r.recommendations))
    return "\n".join(parts)


def _format_flight_line(a: Arrival) -> str:
    """Format a single flight in flight-board style (2 lines for mobile)."""
    sched = a.scheduled_time.strftime("%H:%M")
    ident = escape(a.identifier)
    origin = escape(a.origin)

    if a.is_cancelled:
        return (
            f"  {ident} ← {origin}\n"
            f"  {sched}  ❌ Cancelled"
        )

    if a.delay_minutes >= 5:
        est = a.effective_time.strftime("%H:%M")
        return (
            f"  {ident} ← {origin}\n"
            f"  {sched} → {est}  ⏱ +{a.delay_minutes}m"
        )

    return (
        f"  {ident} ← {origin}\n"
        f"  {sched}  ✅ On Time"
    )


# ── Paginated flights ─────────────────────────────────────────────────────────

def _flights_header(flights: list[Arrival]) -> str:
    active = [a for a in flights if not a.is_cancelled]
    cancelled = len(flights) - len(active)
    count_label = f"{len(active)} arrival{'s' if len(active)!=1 else ''}"
    if cancelled:
        count_label += f", {cancelled} cancelled"
    return count_label


def format_flights_page(
    flights: list[Arrival],
    ok: bool,
    page: int = 0,
    header_title: str = "✈️ <b>Flights — Luxembourg-Findel</b>",
) -> tuple[str, int]:
    """Return (text, total_pages) for a page of flights."""
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    ts = now.strftime("%A %d %b %Y, %H:%M")
    if not ok:
        return f"{header_title}\n🕐 {ts}\n\n  ⚠️ Data unavailable", 1
    if not flights:
        return f"{header_title}\n🕐 {ts}\n\n  No upcoming flights", 1

    total_pages = max(1, (len(flights) + _FLIGHTS_PER_PAGE - 1) // _FLIGHTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * _FLIGHTS_PER_PAGE
    end = start + _FLIGHTS_PER_PAGE
    page_flights = flights[start:end]

    count_label = _flights_header(flights)
    lines = [
        header_title,
        f"🕐 {ts}",
        f"({count_label})",
    ]
    if total_pages > 1:
        lines.append(f"📄 Page {page + 1}/{total_pages}")
    lines.append("")
    for a in page_flights:
        lines.append(_format_flight_line(a))
    return "\n".join(lines), total_pages


def format_flights_report(flights: list[Arrival], ok: bool) -> str:
    """Back-compat: full flights report (page 0, no buttons)."""
    text, _ = format_flights_page(flights, ok)
    return text


# ── TGV ───────────────────────────────────────────────────────────────────────

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
    if next_train is None:
        return ""
    when = _date_label(next_train.effective_time)
    t = next_train.effective_time.strftime("%H:%M")
    delay = f" ⏱+{next_train.delay_minutes}m" if next_train.delay_minutes else ""
    return f"🚆 <b>Next train:</b> {when} {t} — {escape(next_train.identifier)} from {escape(next_train.origin)}{delay}"


def format_next_train_report(next_train: Arrival | None) -> str:
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
    lux_time = tgv.effective_time.strftime("%H:%M")
    day_num = tgv.effective_time.day
    month_name = tgv.effective_time.strftime("%B")
    year = tgv.effective_time.year
    date_str = f"{day_num} {month_name} {year}"
    if tgv.paris_departure:
        paris_time = tgv.paris_departure.strftime("%H:%M")
        return f"{date_str}\nParis {paris_time} → Luxembourg {lux_time}"
    return f"{date_str}\n{lux_time} Paris → Luxembourg"


def _format_next_tgv_line(tgv: Arrival) -> str:
    return f"🚄 <b>Next TGV:</b>\n{_format_tgv_line(tgv)}"


def _line_next_tgv(next_tgv: Arrival | None) -> str:
    if next_tgv is None:
        return ""
    return _format_next_tgv_line(next_tgv)


def _format_tgv_board_line(a: Arrival) -> str:
    """Format a single TGV in flight-board style (2 lines for mobile)."""
    gare_time = a.effective_time.strftime("%H:%M")

    if a.paris_departure:
        paris_time = a.paris_departure.strftime("%H:%M")
        route = f"  Paris ({paris_time}) → Luxembourg ({gare_time})"
    else:
        route = f"  {escape(a.origin)} → Luxembourg ({gare_time})"

    if a.delay_minutes >= 5:
        sched = a.scheduled_time.strftime("%H:%M")
        return f"{route}\n  {sched} → {gare_time}  ⏱ +{a.delay_minutes}m"

    return f"{route}\n  ✅ On Time"


def format_tgv_schedule(tgvs: list[Arrival], day_label: str = "today") -> str:
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    ts = now.strftime("%A %d %b %Y, %H:%M")
    header = "🚄 <b>TGV today</b>"
    sub = "<b>Paris → Luxembourg (Gare Centrale)</b>"
    if not tgvs:
        return f"{header}\n{sub}\n🕐 {ts}\n\n  No TGV in schedule today."
    lines = [
        header,
        sub,
        f"🕐 {ts}",
        f"({len(tgvs)} TGV)",
        "",
    ]
    for a in tgvs:
        lines.append(_format_tgv_board_line(a))
    return "\n".join(lines)


def format_next_tgv(tgv: Arrival | None) -> str:
    if tgv is None:
        return (
            "🚄 <b>Next TGV Paris → Luxembourg</b>\n\n"
            "No TGV found. This can mean no TGVs left today, or train data could not be loaded."
        )
    return "\n\n" + _format_next_tgv_line(tgv)


# ── Section builders ──────────────────────────────────────────────────────────

def _section_flights_now(
    arrivals: list[Arrival],
    status: SourceStatus,
    peaks: list[DemandPeak],
    next_arrival: Arrival | None,
) -> str:
    header = "✈️ <b>Flights (Luxembourg-Findel)</b>"
    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    if not arrivals:
        if next_arrival:
            t = next_arrival.effective_time.strftime("%H:%M")
            return (
                f"{header}\n  Nothing in the next 3h\n"
                f"  Next: {t} — {escape(next_arrival.identifier)}\n"
                f"  from {escape(next_arrival.origin)}\n"
            )
        return f"{header}\n  No upcoming flights\n"
    active = [a for a in arrivals if not a.is_cancelled]
    cancelled = len(arrivals) - len(active)
    count_str = str(len(active))
    if cancelled:
        count_str += f", {cancelled} cancelled"
    lines = [f"{header}", f"({count_str})", ""]
    for a in arrivals[:_FLIGHTS_PER_PAGE]:
        lines.append(_format_flight_line(a))
    if len(arrivals) > _FLIGHTS_PER_PAGE:
        lines.append(f"\n  <i>+{len(arrivals) - _FLIGHTS_PER_PAGE} more…</i>")
    if peaks:
        lines.append(f"\n  📈 Peak: {peaks[0].time_slot} ({peaks[0].count} flights)")
    lines.append("")
    return "\n".join(lines)


def _section_trains_now(
    arrivals: list[Arrival],
    status: SourceStatus,
    peaks: list[DemandPeak],
    next_arrival: Arrival | None,
) -> str:
    header = "🚄 <b>TGV (Gare Centrale)</b>"
    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    tgvs = [a for a in arrivals if a.identifier.upper() == "TGV"]
    if not tgvs:
        return f"{header}\n  No TGV in the next 3h\n"
    lines = [f"{header}", ""]
    for a in tgvs:
        lines.append(_format_tgv_board_line(a))
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

    is_trains = any(a.transport_type.value == "train" for a in arrivals)
    if is_trains:
        return _section_trains_tgv_only(arrivals, header)

    # Flights — show first page only, handler adds pagination buttons
    text, total_pages = format_flights_page(
        arrivals, ok=True, page=0, header_title=header,
    )
    if total_pages > 1:
        text += f"\n\n  <i>Page 1/{total_pages} — use buttons below</i>"
    return text + "\n"


def _section_trains_tgv_only(arrivals: list[Arrival], header: str) -> str:
    tgvs = [a for a in arrivals if a.identifier.upper() == "TGV"]
    header = "🚄 <b>TGV (Gare Centrale)</b>"
    if not tgvs:
        return f"{header}\n  No TGV scheduled\n"
    lines = [f"{header} ({len(tgvs)})", ""]
    for a in tgvs:
        lines.append(_format_tgv_board_line(a))
    lines.append("")
    return "\n".join(lines)


def _section_trains_by_block(
    trains: list[Arrival],
    status: SourceStatus,
    blocks: list[TimeBlock],
) -> str:
    header = "🚄 <b>TGV (Gare Centrale)</b>"
    if status == SourceStatus.UNAVAILABLE:
        return f"{header}\n  ⚠️ Data unavailable\n"
    tgvs = [a for a in trains if a.identifier.upper() == "TGV"]
    if not tgvs:
        return f"{header}\n  No TGV scheduled\n"
    lines = [f"{header} ({len(tgvs)})", ""]
    for a in tgvs:
        lines.append(_format_tgv_board_line(a))
    lines.append("")
    return "\n".join(lines)


def _section_time_blocks(blocks: list[TimeBlock]) -> str:
    lines = ["📊 <b>By Period</b>"]
    for b in blocks:
        fl = sum(1 for a in b.arrivals if a.transport_type.value == "flight" and not a.is_cancelled)
        tgv = sum(1 for a in b.arrivals if a.transport_type.value == "train" and a.identifier.upper() == "TGV")
        total = fl + tgv
        if total == 0:
            lines.append(f"  ▫ {b.label}: quiet")
        else:
            detail = "  ".join(
                p for p in [f"{fl} ✈️" if fl else "", f"{tgv} 🚄" if tgv else ""] if p
            )
            lines.append(f"  ▸ {b.label}: {total} arrival{'s' if total != 1 else ''}  ({detail})")
    lines.append("")
    return "\n".join(lines)


def _section_recs(recs: list[str]) -> str:
    if not recs:
        return "🚖 <b>Tip:</b>\nStandard positioning"
    lines = ["🚖 <b>Positioning Tips</b>", ""]
    for r in recs:
        lines.append(f"▸ {r}")
    return "\n".join(lines)


def format_tgv_message(
    trains: list[Arrival],
    trains_ok: bool,
    *,
    next_tgv: Arrival | None = None,
    title: str = "🚄 <b>TGV — Gare Centrale</b>",
) -> str:
    """Standalone TGV message (message 2 of 3)."""
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    ts = now.strftime("%H:%M")
    if not trains_ok:
        return f"{title}\n🕐 {ts}\n\n  ⚠️ Data unavailable"
    tgvs = [a for a in trains if a.identifier.upper() == "TGV"]
    if not tgvs:
        if next_tgv:
            nxt = _format_next_tgv_line(next_tgv)
            return f"{title}\n🕐 {ts}\n\n  No TGV in this window\n\n{nxt}"
        return f"{title}\n🕐 {ts}\n\n  No TGV scheduled"
    lines = [title, f"🕐 {ts}", f"({len(tgvs)} TGV)", ""]
    for a in tgvs:
        lines.append(_format_tgv_board_line(a))
    if next_tgv and next_tgv not in tgvs:
        lines.append("")
        lines.append(_format_next_tgv_line(next_tgv))
    return "\n".join(lines)


def format_taxi_tip(
    flights: list[Arrival],
    trains: list[Arrival],
    flights_ok: bool,
    trains_ok: bool,
) -> str:
    """Smart taxi positioning tip (message 3 of 3).

    Analyses 30-min slots to find peak demand windows for Airport and Gare Centrale.
    """
    now = datetime.now(tz=pytz.timezone("Europe/Luxembourg"))
    active_fl = [a for a in flights if not a.is_cancelled] if flights_ok else []
    tgvs = [a for a in trains if a.identifier.upper() == "TGV"] if trains_ok else []

    lines = ["🚖 <b>Taxi Tip</b>", ""]

    # Find best 30-min slots for Airport
    airport_slots = _best_slots(active_fl, "Airport ✈️")
    gare_slots = _best_slots(tgvs, "Gare Centrale 🚄")

    if airport_slots:
        for slot_text in airport_slots:
            lines.append(f"▸ {slot_text}")
    if gare_slots:
        for slot_text in gare_slots:
            lines.append(f"▸ {slot_text}")

    if not airport_slots and not gare_slots:
        # No arrivals — give general advice
        next_fl = _first_future(active_fl, now)
        next_tgv = _first_future(tgvs, now)
        if next_fl or next_tgv:
            lines.append("Quiet period right now")
            if next_fl:
                lines.append(f"▸ Airport: first flight at {next_fl.effective_time.strftime('%H:%M')}")
            if next_tgv:
                lines.append(f"▸ Gare: next TGV at {next_tgv.effective_time.strftime('%H:%M')}")
        else:
            lines.append("No upcoming arrivals — rest or reposition")

    return "\n".join(lines)


def _best_slots(arrivals: list[Arrival], location: str) -> list[str]:
    """Find the top 1-2 busiest 30-min slots and return human-readable tips."""
    if not arrivals:
        return []
    from collections import Counter
    slots: Counter[str] = Counter()
    slot_times: dict[str, datetime] = {}
    for a in arrivals:
        t = a.effective_time
        half = "00" if t.minute < 30 else "30"
        key = f"{t.strftime('%H:')}{ half}"
        slots[key] += 1
        if key not in slot_times:
            slot_times[key] = t
    tips: list[str] = []
    for slot, count in slots.most_common(2):
        if count < 1:
            break
        tips.append(f"{location} — {slot} ({count} arrival{'s' if count != 1 else ''})")
    return tips


def _first_future(arrivals: list[Arrival], now: datetime) -> Arrival | None:
    future = [a for a in arrivals if a.effective_time > now]
    return min(future, key=lambda a: a.effective_time) if future else None


def _both_down(r: Report) -> bool:
    return (
        r.flights_status == SourceStatus.UNAVAILABLE
        and r.trains_status == SourceStatus.UNAVAILABLE
    )
