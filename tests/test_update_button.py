"""The Update button must refresh data before building the view (no API calls)."""

from __future__ import annotations

import asyncio

from taxibot import views


class _FakePipeline:
    def __init__(self, hang: bool = False) -> None:
        self.calls: list[str] = []
        self._hang = hang
        self._schedule_cache: dict = {}

    async def refresh_realtime(self) -> None:
        self.calls.append("realtime")

    async def refresh_schedule(self) -> None:
        self.calls.append("schedule")
        if self._hang:
            await asyncio.sleep(3600)
        self._schedule_cache["flights_today"] = ([], True)
        self._schedule_cache["trains_today"] = ([], True)

    def _cache_has_today(self) -> bool:
        return "flights_today" in self._schedule_cache


def test_update_refreshes_realtime_then_schedule_then_builds(monkeypatch) -> None:
    built: list[list[str]] = []

    async def fake_build(pipeline):
        built.append(list(pipeline.calls))
        return ["msg"]

    monkeypatch.setattr(views, "build_soon_messages", fake_build)
    pipeline = _FakePipeline()

    result = asyncio.run(views.build_fresh_soon_messages(pipeline))

    assert result == ["msg"]
    assert built == [["realtime", "schedule"]]


def test_update_falls_back_to_cache_when_refresh_hangs(monkeypatch) -> None:
    async def fake_build(pipeline):
        return ["cached"]

    monkeypatch.setattr(views, "build_soon_messages", fake_build)
    monkeypatch.setattr(views, "_CACHE_WARMUP_TIMEOUT", 0.05)

    result = asyncio.run(views.build_fresh_soon_messages(_FakePipeline(hang=True)))

    assert result == ["cached"]
