"""Test TGV formatting (no API calls)."""

from __future__ import annotations

from datetime import datetime

import pytz

from taxibot.formatters.report import format_next_tgv, format_tgv_schedule
from taxibot.models import Arrival, TransportType

_LUX = pytz.timezone("Europe/Luxembourg")


def test_format_next_tgv_shows_both_times() -> None:
    """format_next_tgv shows Paris dep and Luxembourg arr when paris_departure is set."""
    lux_arrival = _LUX.localize(datetime(2026, 3, 2, 14, 51))
    paris_dep = _LUX.localize(datetime(2026, 3, 2, 12, 39))
    tgv = Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=lux_arrival,
        identifier="TGV",
        origin="Paris Est",
        paris_departure=paris_dep,
    )
    msg = format_next_tgv(tgv)
    assert "12:39" in msg
    assert "14:51" in msg
    assert "Paris" in msg
    assert "Luxembourg" in msg


def test_format_next_tgv_luxembourg_only_when_no_paris() -> None:
    """When paris_departure is missing, only Luxembourg time is shown."""
    lux_arrival = _LUX.localize(datetime(2026, 3, 2, 14, 51))
    tgv = Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=lux_arrival,
        identifier="TGV",
        origin="Marseille",
    )
    msg = format_next_tgv(tgv)
    assert "14:51" in msg
    assert "Luxembourg" in msg
    assert "→ Luxembourg" in msg


def test_format_next_tgv_includes_date_with_year() -> None:
    """Format includes full date (day month year) for TGV next."""
    lux_arrival = _LUX.localize(datetime(2026, 3, 5, 14, 40))
    tgv = Arrival(
        transport_type=TransportType.TRAIN,
        scheduled_time=lux_arrival,
        identifier="TGV",
        origin="Paris Est",
    )
    msg = format_next_tgv(tgv)
    assert "5 March 2026" in msg
    assert "14:40" in msg
    assert "Paris" in msg
    assert "Luxembourg" in msg


def test_format_tgv_schedule_includes_date_with_year() -> None:
    """TGV today schedule uses same date format (day month year) per line."""
    tgvs = [
        Arrival(
            transport_type=TransportType.TRAIN,
            scheduled_time=_LUX.localize(datetime(2026, 3, 5, 10, 25)),
            identifier="TGV",
            origin="Paris Est",
        ),
        Arrival(
            transport_type=TransportType.TRAIN,
            scheduled_time=_LUX.localize(datetime(2026, 3, 5, 14, 51)),
            identifier="TGV",
            origin="Paris Est",
        ),
    ]
    msg = format_tgv_schedule(tgvs, "today")
    assert "5 March 2026" in msg
    assert "10:25" in msg
    assert "14:51" in msg
    assert "Paris" in msg
    assert "Luxembourg" in msg
