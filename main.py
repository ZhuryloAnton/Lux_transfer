#!/usr/bin/env python3
"""TaxiBOT Luxembourg — entry point."""

from __future__ import annotations

import logging

from config.settings import get_settings
from bot.application import create_application


def main() -> None:
    settings = get_settings()
    _setup_logging(settings.log_level)

    logger = logging.getLogger("taxibot")
    logger.info("Starting TaxiBOT Luxembourg…")

    app = create_application(settings)
    app.run_polling(drop_pending_updates=True)


def _setup_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Silence noisy third-party loggers
    for name in ("httpx", "apscheduler", "aiohttp", "telegram"):
        logging.getLogger(name).setLevel(logging.WARNING)


if __name__ == "__main__":
    main()
