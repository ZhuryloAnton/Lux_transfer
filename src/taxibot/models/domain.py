"""Domain models — pure dataclasses, no framework dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class TransportType(str, Enum):
    FLIGHT = "flight"
    TRAIN = "train"


class SourceStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


@dataclass
class Arrival:
    transport_type: TransportType
    scheduled_time: datetime       # always tz-aware, Europe/Luxembourg
    identifier: str                # e.g. "LX123", "IC", "TGV"
    origin: str                    # city / airport the service comes from
    status: str = "scheduled"
    delay_minutes: int = 0
    # Departure time at the service's ORIGIN, not necessarily Paris — SNCF TGVs
    # terminating at Luxembourg also start from Marseille and Montpellier. The
    # field name is historical; read it together with `origin`.
    paris_departure: datetime | None = None

    @property
    def is_cancelled(self) -> bool:
        return self.status == "CX"

    @property
    def is_tgv(self) -> bool:
        """True for any TGV service.

        Substring match, not equality: feeds return "TGV", but also "TGV 9573"
        and "TGV INOUI" depending on the source. This used to be tested two
        different ways — `== "TGV"` in the formatters and `"TGV" in ...` in the
        pipeline — so a feed using long names showed TGVs in /tgv and nothing
        anywhere else. One definition, one behaviour.
        """
        return "TGV" in (self.identifier or "").upper()

    @property
    def effective_time(self) -> datetime:
        return self.scheduled_time + timedelta(minutes=self.delay_minutes)


@dataclass
class DemandPeak:
    time_slot: str   # "14:30"
    count: int
    location: str    # "Airport" | "Gare Centrale"


@dataclass
class TimeBlock:
    label: str
    start_hour: int
    end_hour: int
    arrivals: list[Arrival] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.arrivals)


@dataclass
class Report:
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    flights: list[Arrival] = field(default_factory=list)
    trains: list[Arrival] = field(default_factory=list)
    flight_peaks: list[DemandPeak] = field(default_factory=list)
    train_peaks: list[DemandPeak] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    flights_status: SourceStatus = SourceStatus.UNAVAILABLE
    trains_status: SourceStatus = SourceStatus.UNAVAILABLE
    time_blocks: list[TimeBlock] = field(default_factory=list)
    next_flight: Arrival | None = None
    next_train: Arrival | None = None
    next_tgv: Arrival | None = None
