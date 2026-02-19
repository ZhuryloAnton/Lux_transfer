from __future__ import annotations

import html as htmllib
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import pytz

from src.models import Event
from src.utils.cache import cached
from src.utils.http import fetch_json, get_session

logger = logging.getLogger(__name__)

LUX_TZ = pytz.timezone("Europe/Luxembourg")

LCTO_URL = "https://www.luxembourg-city.com/en/what-s-on"

ROCKHAL_API = "https://rockhal.lu/wp-json/wp/v2/show"

HIGH_IMPACT_KEYWORDS = frozenset({
    "concert", "festival", "rock", "pop", "hip-hop", "rap", "electro",
    "metal", "jazz", "live music", "dj set", "party", "rave", "gala",
    "sport", "match", "football", "rugby", "marathon",
})
MEDIUM_KEYWORDS = frozenset({
    "exhibition", "theatre", "opera", "ballet", "dance",
    "comedy", "circus", "performance", "spectacle",
})

LARGE_VENUES = frozenset({
    "rockhal", "philharmonie", "luxexpo", "den atelier",
    "coque", "stade", "d'coque", "d'coque",
    "grand théâtre", "théâtre municipal",
})


class EventDataSource:
    """Collects major events in Luxembourg from real sources.

    Sources:
    - Luxembourg-city.com (LCTO) event listing
    - Rockhal concert schedule (WP REST + page scraping)
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("source.events")

    @cached("events_all")
    async def get_events(self) -> list[Event]:
        lcto = await self._fetch_lcto()
        rockhal = await self._fetch_rockhal()
        all_events = lcto + rockhal
        all_events = _deduplicate(all_events)
        all_events.sort(key=lambda e: e.date)
        return all_events

    async def get_today_tomorrow(self) -> list[Event]:
        events = await self.get_events()
        now = datetime.now(tz=LUX_TZ)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_end = (today_start + timedelta(days=2))
        future = [
            e for e in events
            if today_start <= e.date < tomorrow_end
        ]
        return future

    async def _fetch_lcto(self) -> list[Event]:
        try:
            session = await get_session()
            async with session.get(LCTO_URL, ssl=False) as resp:
                resp.raise_for_status()
                page = await resp.text()
        except Exception as exc:
            self.logger.warning("LCTO fetch failed: %s", exc)
            return []
        return self._parse_lcto(page)

    def _parse_lcto(self, page: str) -> list[Event]:
        cards = re.findall(
            r'<a[^>]*href=["\']([^"\']*what-s-on/event/[^"\']+)["\'][^>]*>'
            r'((?:(?!</a>).)*\d{2}\.\d{2}\.\d{4}(?:(?!</a>).)*)</a>',
            page, re.DOTALL,
        )
        events: list[Event] = []
        for link, content in cards:
            e = self._parse_lcto_card(link, content)
            if e is not None:
                events.append(e)
        self.logger.info("LCTO: parsed %d events", len(events))
        return events

    @staticmethod
    def _parse_lcto_card(link: str, content: str) -> Event | None:
        date_m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", content)
        if not date_m:
            return None
        try:
            dt = LUX_TZ.localize(datetime(
                int(date_m.group(3)), int(date_m.group(2)), int(date_m.group(1)),
            ))
        except ValueError:
            return None

        text = re.sub(r"<[^>]+>", "\n", content)
        lines = [htmllib.unescape(l.strip()) for l in text.split("\n") if l.strip()]
        if len(lines) < 2:
            return None

        category = lines[0] if lines[0] != date_m.group(0) else ""
        title_idx = next((i for i, l in enumerate(lines) if l != category and not re.match(r"\d{2}\.\d{2}\.\d{4}", l)), -1)
        title = lines[title_idx] if title_idx >= 0 else "Unknown Event"
        venue = lines[title_idx + 1] if title_idx + 1 < len(lines) else ""

        impact = _estimate_impact(title, venue, category)

        return Event(
            name=title[:80],
            venue=venue[:60],
            date=dt,
            start_time="",
            end_time="",
            category=category,
            demand_impact=impact,
            source="LCTO",
        )

    async def _fetch_rockhal(self) -> list[Event]:
        try:
            shows = await fetch_json(
                ROCKHAL_API,
                params={
                    "per_page": "50",
                    "orderby": "date",
                    "order": "desc",
                    "_fields": "id,link,title,yoast_head_json,class_list",
                },
                ssl=False,
            )
        except Exception as exc:
            self.logger.warning("Rockhal API failed: %s", exc)
            return []

        if not isinstance(shows, list):
            return []

        events: list[Event] = []
        now = datetime.now(tz=LUX_TZ)
        upcoming_urls: list[tuple[str, str, list]] = []

        for s in shows:
            title = s.get("title", {}).get("rendered", "")
            link = s.get("link", "")
            classes = s.get("class_list", [])
            status_tags = [c.replace("roc_show_status-", "") for c in classes if "status" in c]
            if "sold-out" not in status_tags and "cancelled" not in status_tags:
                upcoming_urls.append((link, title, classes))

        for link, title, classes in upcoming_urls[:20]:
            e = await self._scrape_rockhal_show(link, title, classes)
            if e is not None and e.date >= now.replace(hour=0, minute=0, second=0, microsecond=0):
                events.append(e)

        self.logger.info("Rockhal: parsed %d upcoming events", len(events))
        return events

    async def _scrape_rockhal_show(self, url: str, title: str, classes: list) -> Event | None:
        try:
            session = await get_session()
            async with session.get(url, ssl=False, timeout=8) as resp:
                if resp.status != 200:
                    return None
                page = await resp.text()
        except Exception:
            return None

        details = re.findall(
            r'class=["\'][^"\']*show-detail[^"\']*["\'][^>]*>(.*?)</div>',
            page, re.DOTALL | re.I,
        )
        date_str = ""
        time_str = ""
        for d in details:
            text = re.sub(r"<[^>]+>", " ", d).strip()
            text = " ".join(text.split())
            date_m = re.search(r"(\w{3}\s+\d{1,2}\s+\w{3,9}\s+\d{4})", text)
            if date_m:
                date_str = date_m.group(1)
            time_m = re.search(r"-\s*(\d{1,2}:\d{2})", text)
            if time_m:
                time_str = time_m.group(1)

        if not date_str:
            return None

        try:
            dt = LUX_TZ.localize(datetime.strptime(date_str, "%a %d %b %Y"))
        except ValueError:
            return None

        genres = [c.replace("roc_show_genre-", "") for c in classes if "genre" in c]
        category = genres[0].replace("-", "/") if genres else "Concert"

        return Event(
            name=htmllib.unescape(title)[:80],
            venue="Rockhal",
            date=dt,
            start_time=time_str,
            end_time="",
            category=category.title(),
            demand_impact="high",
            source="Rockhal",
        )


def _estimate_impact(title: str, venue: str, category: str) -> str:
    v_lower = venue.lower()
    cat_lower = category.lower()
    title_lower = title.lower()
    if any(lv in v_lower for lv in LARGE_VENUES):
        return "high"
    if cat_lower in ("music", "concert", "festival"):
        return "high" if any(lv in v_lower for lv in LARGE_VENUES) else "medium"
    if any(k in title_lower for k in HIGH_IMPACT_KEYWORDS):
        return "medium"
    if any(k in cat_lower for k in MEDIUM_KEYWORDS) or any(k in v_lower for k in MEDIUM_KEYWORDS):
        return "medium"
    return "low"


def _deduplicate(events: list[Event]) -> list[Event]:
    seen: set[str] = set()
    unique: list[Event] = []
    for e in events:
        key = f"{e.name.lower()[:30]}_{e.date.strftime('%Y%m%d')}"
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique
