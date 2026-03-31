# TaxiBOT Luxembourg

Telegram bot for taxi demand forecasts in Luxembourg City: flights (Luxembourg-Findel) and trains (Gare Centrale), including TGV Paris → Luxembourg.

## Requirements

- Python 3.12+
- A Telegram Bot Token and the chat ID where reports are sent

## Setup

1. **Clone and install**

   ```bash
   pip install -r requirements.txt
   # or: pip install -e .
   ```

2. **Configuration**

   Copy `.env.example` to `.env` and set at least:

   - `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `TELEGRAM_CHAT_ID` — chat or user ID that receives reports

   For train data, set either:

   - **Option A (recommended):** `OPEN_DATA_API` — full Mobiliteit.lu departureBoard URL (see `.env.example`)
   - **Option B:** `GTFS_URL` — path or URL to a GTFS zip

   Optional: `GTFS_RT_URL`, `REALTIME_REFRESH_SECONDS`, `REPORT_INTERVAL_HOURS`, `LOG_LEVEL`.

3. **Run**

   From the project root:

   ```bash
   python main.py
   # or: python -m taxibot
   ```

   Ensure `PYTHONPATH` includes `src` if you run from another directory (e.g. `PYTHONPATH=src python main.py`).

## Production

- **Docker:** `docker compose up -d` (uses `.env` and builds from `Dockerfile`). Logs are limited (json-file, 10MB, 3 files).
- **Secrets:** Keep `.env` out of version control (it is in `.gitignore`). Do not commit tokens or API keys.
- **Behaviour:** Schedule (flights + trains) is cached and refreshed every 10 minutes; reports are served from cache for fast responses. First request or after a cold start may trigger one fetch.
- **Health:** Use the `/status` command in Telegram to confirm the bot is running.

## Commands

- `/start` — show keyboard
- `/report` — next 3 hours
- `/today`, `/tomorrow` — full-day schedule
- `/flights` — today’s flights
- `/next_train`, `/next_tgv`, `/tgv` — train/TGV info
- `/status` — health check
- `/help` — help text
