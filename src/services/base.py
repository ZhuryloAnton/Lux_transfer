from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any


class DataSourceError(Exception):
    """No real data could be obtained from any endpoint."""


class BaseDataSource(ABC):
    """Contract for every data source.

    Rules:
    - Return ONLY real, validated data from live APIs or official schedules.
    - NEVER generate mock, simulated, or hardcoded data.
    - If all endpoints fail, raise DataSourceError.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.logger = logging.getLogger(f"source.{name}")

    @abstractmethod
    async def fetch_raw(self) -> Any:
        ...

    @abstractmethod
    async def parse(self, raw: Any) -> list[Any]:
        ...

    @abstractmethod
    async def validate(self, items: list[Any]) -> list[Any]:
        ...

    async def get_data(self) -> list[Any]:
        try:
            raw = await self.fetch_raw()
            parsed = await self.parse(raw)
            validated = await self.validate(parsed)
            self.logger.info(
                "'%s': parsed=%d, validated=%d",
                self.name, len(parsed), len(validated),
            )
            return validated
        except DataSourceError:
            self.logger.warning("'%s': no real-time data available", self.name)
            return []
        except Exception:
            self.logger.exception("'%s': unexpected failure", self.name)
            return []
