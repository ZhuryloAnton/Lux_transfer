# Verifying bot times (flights and trains)

## Flight schedule (airplane arrivals)

### How it works

- **Source:** The bot uses the **same API** as the official Luxembourg Airport arrivals page:  
  `https://luxair-flightdata-1.azurewebsites.net/api/v1/Flights`  
  (with `Day=YYYY-MM-DD`, `Sens=A` for arrivals).
- **Official site:** [Luxembourg Airport – Arrivals](https://www.lux-airport.lu/en/flights/arrivals/) loads data from this API.
- So the **schedule times in the bot are the same** as on the airport website: same scheduled time (`schDate`), same estimated time when available (`timeEstimated`), and the bot shows **effective arrival time** = scheduled + delay (delay from estimated − scheduled).
- **Filtering:** Flights already landed (AR, LD) or cancelled (CX) are excluded. The bot shows upcoming arrivals for today (and after 21:00 also early tomorrow).

### Do the times match?

Yes. The bot does not use a different timetable; it calls the same backend as the airport’s own arrivals page. So:

- **Scheduled time** in the bot = official scheduled arrival at Luxembourg (Findel).
- **Delay** = difference between estimated and scheduled when the API returns `timeEstimated`.
- You can check any flight on [lux-airport.lu → Arrivals](https://www.lux-airport.lu/en/flights/arrivals/) for the same day and compare time and flight number.

---

## Train schedule (GTFS open data)

### How it works

- **Source (pick one):**
  - **Open Data API:** Set `OPEN_DATA_API` in `.env` to the **full URL** of an endpoint that returns train departures as JSON (e.g. a REST API that returns an array of departures, or an object with a key like `departures` / `data`). The bot uses the same list for all trains; **next TGV** is the same data filtered to lines whose name contains “TGV”. Expected fields per item: time (e.g. `departureTime`, `scheduledTime`), line (e.g. `lineName`, `routeShortName`), origin (e.g. `direction`, `destination`). Optional: `delay` (minutes).
  - **GTFS:** If `OPEN_DATA_API` is not set, the bot uses **Luxembourg public transport GTFS** (no API key). Set `GTFS_URL` to a zip path or URL. Default: [OpenOV Luxembourg GTFS](http://openov.lu/data/gtfs/gtfs-openov-lu.zip) (may have limited date range).
- **Coverage:** All rail arrivals at Gare Centrale. TGV is identified by route/line name containing “TGV”.

### How to verify manually

1. **Run the verification script** (from project root):
   ```bash
   PYTHONPATH=src python3 scripts/verify_tgv_times.py
   ```
   This prints the next TGV’s Luxembourg arrival time (and origin from the feed).

2. **Compare with the official timetable:**
   - [mobiliteit.lu → Plan a trip](https://www.mobiliteit.lu/en/plan-a-trip/) or [CFL timetable](https://www.cfl.lu/en-gb/timetable)
   - From: **Paris** (or Paris Gare de l’Est) → To: **Luxembourg**
   - Date: same day as in the script output

3. **Real-time delays:** The bot merges **GTFS-RT** trip updates (e.g. from OpenOV) with the static GTFS schedule. Delays are refreshed every 10 minutes. If a train is delayed, the report shows the updated time and “⏱+Nm” (delay in minutes). Same source as mobiliteit.lu real-time data.

4. **Note:** Paris departure time is not in the GTFS feed, so the bot shows Luxembourg arrival (and origin) only for TGV from the feed.
