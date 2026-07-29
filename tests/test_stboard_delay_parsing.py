"""HAFAS stboard delay-field parsing.

Every string below was taken from a live board on 2026-07-29, not invented. The
field is loosely typed: "0", "3", "+ 5" (sign and space) and "cancel" all appeared
in a single reply. Two of those used to parse as "no delay", so a delayed train
and a cancelled one both rendered as "✅ On Time".
"""

from __future__ import annotations

from datetime import datetime

import pytz

from taxibot.services.trains_opendata import _parse_journey

_LUX = pytz.timezone("Europe/Luxembourg")
_BASE = _LUX.localize(datetime(2026, 7, 29, 9, 39))


def _journey(delay: str, *, time: str = "10:15", prod: str = "RE 10459#RE") -> str:
    return f'fpTime="{time}" fpDate="29.07.26" delay="{delay}" prod="{prod}" dir="Trier, Gare"'


def test_plain_numeric_delay() -> None:
    a = _parse_journey(_journey("3"), _BASE)
    assert a is not None
    assert a.delay_minutes == 3
    assert not a.is_cancelled


def test_zero_delay_is_on_time() -> None:
    a = _parse_journey(_journey("0"), _BASE)
    assert a is not None
    assert a.delay_minutes == 0
    assert not a.is_cancelled


def test_dash_means_no_realtime_data() -> None:
    """Future arrivals report "-" — absence of data, not an on-time guarantee."""
    a = _parse_journey(_journey("-"), _BASE)
    assert a is not None
    assert a.delay_minutes == 0


def test_signed_delay_with_space() -> None:
    """The regression: int("+ 5") raises, so this used to become 0."""
    a = _parse_journey(_journey("+ 5"), _BASE)
    assert a is not None
    assert a.delay_minutes == 5


def test_signed_delay_without_space() -> None:
    a = _parse_journey(_journey("+12"), _BASE)
    assert a is not None
    assert a.delay_minutes == 12


def test_cancel_sets_cancelled_status() -> None:
    """The worse regression: a cancelled train rendered as on-time."""
    a = _parse_journey(_journey("cancel", time="13:49", prod="IC 2110#IC"), _BASE)
    assert a is not None
    assert a.is_cancelled
    assert a.delay_minutes == 0


def test_unknown_token_degrades_to_no_delay() -> None:
    """An unrecognised value must not crash the whole board parse."""
    a = _parse_journey(_journey("???"), _BASE)
    assert a is not None
    assert a.delay_minutes == 0
    assert not a.is_cancelled


def test_negative_delay_clamped_to_zero() -> None:
    """Early arrivals are not negative delays; the board is about lateness."""
    a = _parse_journey(_journey("-3"), _BASE)
    assert a is not None
    assert a.delay_minutes == 0


def test_non_train_product_is_dropped() -> None:
    a = _parse_journey(_journey("0", prod="Bus 118#Bus"), _BASE)
    assert a is None
