#!/usr/bin/env python3
"""TaxiBOT Luxembourg — Taxi demand forecasting Telegram bot."""

from __future__ import annotations

from src.bot import create_application
from src.config import get_settings, setup_logging
from src.utils.http import close_session


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    import logging
    logger = logging.getLogger("taxibot")
    logger.info("Starting TaxiBOT Luxembourg…")

    app = create_application(settings)

    async def on_shutdown(_: object) -> None:
        await close_session()

    app.post_shutdown = on_shutdown  # type: ignore[assignment]
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
