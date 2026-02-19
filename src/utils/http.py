from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None

MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        _session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "TaxiBOT/2.0 (Luxembourg Taxi Forecast)"},
        )
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


async def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    retries: int = MAX_RETRIES,
    ssl: bool | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    session = await get_session()
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, params=params, ssl=ssl, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt < retries:
                wait = RETRY_BACKOFF ** attempt
                logger.warning(
                    "%s attempt %d/%d failed (%s), retry in %.0fs",
                    url, attempt, retries, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error("%s failed after %d attempts: %s", url, retries, exc)
    raise last_exc  # type: ignore[misc]
