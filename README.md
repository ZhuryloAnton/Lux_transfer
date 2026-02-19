# TaxiBOT Luxembourg

Telegram bot that forecasts taxi demand in Luxembourg City using **real-time data only** from flight arrivals and train schedules.

## Features

- **Two-button interface** — "Schedule Now (Next 3 Hours)" and "Tomorrow Schedule"
- **Real data only** — OpenSky Network (flights), DB HAFAS + Luxembourg GTFS (trains)
- **Zero mock data** — if a source is unreachable, the report says so explicitly
- **Validation pipeline** — only future arrivals with valid timestamps pass through
- **Auto-reports** — sends the 3-hour forecast every 3 hours automatically

## Data Sources

| Source | Data | Type |
|--------|------|------|
| [OpenSky Network](https://opensky-network.org) | Flight arrivals at ELLX | REST API, free |
| [DB HAFAS](https://v6.db.transport.rest) | Train arrivals at Gare Centrale | REST API, free |
| [Luxembourg GTFS](https://data.public.lu) | Official CFL train schedules | Static timetable, free |

## Project Structure

```
TaxiBOT/
├── main.py                       # Entry point
├── src/
│   ├── bot.py                    # Telegram app + job queue setup
│   ├── config.py                 # Settings via .env (pydantic-settings)
│   ├── models.py                 # Arrival, Report, TimeBlock, DemandPeak
│   ├── handlers/
│   │   ├── commands.py           # 2-button ReplyKeyboard + /report /tomorrow
│   │   └── scheduler.py          # Auto-report every 3 hours
│   ├── services/
│   │   ├── base.py               # Abstract base — real-data-only contract
│   │   ├── flights.py            # OpenSky API for Luxembourg Airport
│   │   ├── trains.py             # HAFAS + GTFS for Gare Centrale
│   │   ├── analyzer.py           # Peak detection, time blocks, recommendations
│   │   ├── formatter.py          # Telegram HTML message formatting
│   │   └── report_pipeline.py    # Orchestrates fetch → analyze → format
│   └── utils/
│       ├── cache.py              # TTL cache for API responses
│       └── http.py               # aiohttp with retry + backoff
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── .gitignore
```

## Quick Start

### 1. Create your Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copy the bot token

### 2. Get your chat ID

Message [@userinfobot](https://t.me/userinfobot) → it replies with your ID

### 3. Configure

```bash
cp .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

### 4. Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Or with Docker:

```bash
docker compose up -d --build
```

## Bot Interface

### Buttons (always visible)

| Button | What it does |
|--------|-------------|
| 📊 Schedule Now (Next 3 Hours) | Real-time arrivals, peaks, taxi recommendations |
| 📅 Tomorrow Schedule | Full day grouped by morning/afternoon/evening |

### Slash commands

| Command | Description |
|---------|-------------|
| `/start` | Show the keyboard |
| `/report` | Same as "Schedule Now" |
| `/tomorrow` | Same as "Tomorrow Schedule" |
| `/status` | Health check |
| `/help` | All commands |

## Report Format

### Schedule Now (Next 3 Hours)

The report shows real-time data in this structure:

- Header with current time and 3-hour window
- **✈️ Airport** — each arrival line: time, callsign, origin ICAO code, delay if any; peak 30-min slot
- **🚆 Gare Centrale** — each arrival line: time, train identifier, origin station, delay if any; peak 30-min slot
- **🚖 Recommendation** — positioning advice based on actual arrival density

If a source is unreachable: `⚠️ Real-time flight/train data unavailable`

### Tomorrow Schedule

The report shows scheduled data in this structure:

- Header with tomorrow's date
- **✈️ Flights** — total count, time range, peak slot
- **🚆 Trains** — total count, time range, peak slot
- **📊 By Time Block** — arrivals grouped into Early Morning / Morning / Afternoon / Evening / Night
- **🚖 Recommendation** — shift planning advice based on which block has the most arrivals

If no data: `⚠️ No real-time data available.`

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | required | From BotFather |
| `TELEGRAM_CHAT_ID` | required | Target chat for auto-reports |
| `REPORT_INTERVAL_HOURS` | 3 | Auto-report interval (0 to disable) |
| `CACHE_TTL_SECONDS` | 600 | API response cache lifetime |
| `LOG_LEVEL` | INFO | Logging verbosity |

## Real Data Only Policy

This bot **never** generates mock, simulated, demo, or hardcoded data.

- If OpenSky is down → report says "Real-time flight data unavailable"
- If HAFAS and GTFS both fail → report says "Real-time train data unavailable"
- If all sources fail → report says "No real-time data available."

## License

MIT
