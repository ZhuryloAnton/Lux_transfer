#!/usr/bin/env python3
"""
Verify TGV times from GTFS against the official journey planner.

Usage (from project root; no API key required):
  PYTHONPATH=src python3 scripts/verify_tgv_times.py
  PYTHONPATH=src python3 scripts/verify_tgv_times.py --verbose   # show why no TGV if missing

Optional: set GTFS_URL in .env to override the default Luxembourg GTFS feed.

Then compare the printed times with the same journey on:
  https://www.mobiliteit.lu/en/  (Paris → Luxembourg, same date)
  https://www.cfl.lu/en-gb/timetable
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add src to path when run as script
_root = Path(__file__).resolve().parents[1]
_src = _root / "src"
if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# Load .env from project root (optional GTFS_URL)
os.chdir(_root)
_env_file = _root / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and value:
                os.environ.setdefault(key, value)

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


async def main() -> None:
    from taxibot.core.http import close_session
    from taxibot.services.trains_gtfs import GTFSTrainSource
    from taxibot.services.trains_opendata import OpenDataTrainSource

    open_data_api = os.environ.get("OPEN_DATA_API", "").strip()
    if open_data_api:
        source = OpenDataTrainSource(api_url=open_data_api)
        if VERBOSE:
            print("Using Open Data API:", open_data_api)
    else:
        gtfs_url = os.environ.get("GTFS_URL", "").strip() or "http://openov.lu/data/gtfs/gtfs-openov-lu.zip"
        source = GTFSTrainSource(gtfs_url=gtfs_url)
        if VERBOSE:
            print("Using GTFS:", gtfs_url)

    try:
        if VERBOSE and not open_data_api:
            data = await source._load_gtfs()
            if not data:
                print("GTFS download or parse failed. Check URL and network.")
                return
            n_today = len(data.get("arrivals_today", []))
            n_tomorrow = len(data.get("arrivals_tomorrow", []))
            lux_id = data.get("lux_stop_id", "")
            route_info = data.get("route_info", {})
            today_rows = data.get("arrivals_today", [])
            route_names_today = set()
            for _at, _tid, rid, _fs in today_rows:
                route_names_today.add(route_info.get(rid, rid))
            tgv_today = sum(1 for _at, _tid, rid, _fs in today_rows if (route_info.get(rid) or "").upper().startswith("TGV"))
            tgv_tomorrow = sum(1 for _at, _tid, rid, _fs in data.get("arrivals_tomorrow", []) if (route_info.get(rid) or "").upper().startswith("TGV"))
            print("GTFS loaded successfully.")
            print(f"  Luxembourg stop_id: {lux_id or '(not found)'}")
            print(f"  Arrivals today:     {n_today} (TGV: {tgv_today})")
            print(f"  Arrivals tomorrow:  {n_tomorrow} (TGV: {tgv_tomorrow})")
            if today_rows:
                print(f"  Route names at Luxembourg today: {sorted(route_names_today)}")
            if n_today == 0 and n_tomorrow == 0:
                print()
                print("  The feed likely does not include the current date (check calendar_dates.txt).")
                print("  The default openov.lu feed can have a limited date range.")
                print("  Download the latest GTFS from data.public.lu:")
                print("    https://data.public.lu/en/datasets/horaires-et-arrets-des-transport-publics-gtfs/")
                print("  Then set GTFS_URL in .env to the downloaded zip path (e.g. file:///path/to/gtfs.zip)")
                print("  or use the resource URL from the dataset page if available.")
            print()

        tgv = await source.get_next_tgv()

        if tgv is None:
            print("No upcoming TGV found in the feed (today or tomorrow).")
            print()
            if not VERBOSE:
                print("Run with --verbose to see why (e.g. feed date range, stop, route names):")
                print("  PYTHONPATH=src python3 scripts/verify_tgv_times.py --verbose")
            print("If using GTFS: ensure the feed includes current dates (see GTFS_URL).")
            print("If using Open Data API: check OPEN_DATA_API URL and response format.")
            return

        day = tgv.effective_time.strftime("%A %d %B %Y")
        lux_arr = tgv.effective_time.strftime("%H:%M")
        paris_dep = tgv.paris_departure.strftime("%H:%M") if tgv.paris_departure else "(not in GTFS)"

        print("=" * 60)
        print("NEXT TGV  Paris → Luxembourg (Gare Centrale)")
        print("=" * 60)
        print(f"  Date:        {day}")
        print(f"  Paris dep:   {paris_dep}")
        print(f"  Luxembourg:  {lux_arr} (arrival)")
        print(f"  Origin:      {tgv.origin}")
        print()
        print("Verify on official site:")
        print("  https://www.mobiliteit.lu/en/")
        print("  → Plan a trip: Paris → Luxembourg, date above")
        print("  → Compare the same TGV arrival at Luxembourg")
        print("=" * 60)
    finally:
        await close_session()


if __name__ == "__main__":
    asyncio.run(main())
