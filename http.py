"""Shared aiohttp session with retry and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        _session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "TaxiBOT/3.0 (Luxembourg Taxi Forecast)"},
        )
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


async def fetch_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    ssl: bool = True,
    retries: int = _MAX_RETRIES,
) -> Any:
    """GET a URL and return parsed JSON, with retry on transient errors."""
    session = await get_session()
    last_exc: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            async with session.get(
                url, params=params, ssl=ssl, headers=headers
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt < retries:
                wait = _BACKOFF_BASE**attempt
                logger.warning(
                    "%s attempt %d/%d failed: %s — retrying in %.0fs",
                    url, attempt, retries, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error("%s failed after %d attempts: %s", url, retries, exc)

    raise last_exc  # type: ignore[misc]


async def fetch_bytes(url: str, *, ssl: bool = True) -> bytes:
    """GET a URL and return raw bytes (for binary downloads like GTFS zip)."""
    session = await get_session()
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with session.get(url, ssl=ssl) as resp:
                resp.raise_for_status()
                return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE**attempt
                logger.warning(
                    "bytes fetch %s attempt %d/%d failed: %s — retrying in %.0fs",
                    url, attempt, _MAX_RETRIES, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error("bytes fetch %s failed after %d attempts: %s", url, _MAX_RETRIES, exc)

    raise last_exc  # type: ignore[misc]
