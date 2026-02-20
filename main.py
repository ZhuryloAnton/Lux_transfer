#!/usr/bin/env python3
"""TaxiBOT Luxembourg — Entry point."""

from __future__ import annotations

import logging

from config.settings import Settings
from bot.application import create_application
from utils.http import close_session


def main() -> None:
    settings = Settings()
    _setup_logging(settings.log_level)

    logger = logging.getLogger("taxibot")
    logger.info("Starting TaxiBOT Luxembourg…")

    app = create_application(settings)

    async def on_shutdown(_: object) -> None:
        await close_session()

    app.post_shutdown = on_shutdown  # type: ignore[assignment]
    app.run_polling(drop_pending_updates=True)


def _setup_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


if __name__ == "__main__":
    main()
