"""Shared aiohttp session — one instance for the whole process lifetime."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
_HEADERS = {"User-Agent": "TaxiBOT/4.0 (Luxembourg Taxi Forecast)"}


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS)
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
) -> Any:
    session = await _get_session()
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with session.get(url, params=params, headers=headers, ssl=ssl) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "fetch_json %s attempt %d/%d failed (%s) — retry in %.0fs",
                    url, attempt, _MAX_RETRIES, exc, wait,
                )
                await asyncio.sleep(wait)
    logger.error("fetch_json %s failed after %d attempts: %s", url, _MAX_RETRIES, last_exc)
    raise last_exc  # type: ignore[misc]


async def fetch_bytes(url: str, *, ssl: bool = True) -> bytes:
    session = await _get_session()
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with session.get(url, ssl=ssl) as resp:
                resp.raise_for_status()
                return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "fetch_bytes %s attempt %d/%d failed (%s) — retry in %.0fs",
                    url, attempt, _MAX_RETRIES, exc, wait,
                )
                await asyncio.sleep(wait)
    logger.error("fetch_bytes %s failed after %d attempts: %s", url, _MAX_RETRIES, last_exc)
    raise last_exc  # type: ignore[misc]


async def post_json(
    url: str,
    *,
    json_body: dict | list | None = None,
    headers: dict[str, str] | None = None,
    ssl: bool = True,
) -> Any:
    """POST *url* with a JSON body and return parsed JSON.  Retries on transient errors."""
    session = await _get_session()
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with session.post(url, json=json_body, headers=headers, ssl=ssl) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE ** attempt
                logger.warning("post_json %s attempt %d/%d failed (%s) — retry in %.0fs",
                               url, attempt, _MAX_RETRIES, exc, wait)
                await asyncio.sleep(wait)

    logger.error("post_json %s failed after %d attempts: %s", url, _MAX_RETRIES, last_exc)
    raise last_exc  # type: ignore[misc]
