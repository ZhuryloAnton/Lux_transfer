"""TaxiBOT Luxembourg — Telegram bot for taxi demand forecasts."""

from __future__ import annotations

__version__ = "1.0.0"


def run() -> None:
    """Entry point: load settings, create application, run polling."""
    import time
    from taxibot.application import create_application
    from taxibot.core.config import get_settings
    import logging

    settings = get_settings()
    _setup_logging(settings.log_level)

    logger = logging.getLogger("taxibot")

    # Wait for any previous instance to fully stop (Render restarts overlap)
    logger.info("Waiting 5s for previous instance to stop…")
    time.sleep(5)

    logger.info("Starting TaxiBOT Luxembourg…")

    app = create_application(settings)
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
        pool_timeout=10,
        connect_timeout=10,
        read_timeout=10,
    )


def _setup_logging(level: str) -> None:
    import logging

    level = (level or "INFO").strip().upper()
    numeric = getattr(logging, level, logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for name in ("httpx", "apscheduler", "aiohttp", "telegram"):
        logging.getLogger(name).setLevel(logging.WARNING)
