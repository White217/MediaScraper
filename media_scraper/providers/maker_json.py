"""一本道、天然むすめ、パコパコママ共用的 movie_details JSON。"""

from __future__ import annotations

import json
import re
from typing import Optional

from core.models import Actor, Metadata, ParsedFilename
from providers.page_parse import usable_poster_url, year_of
from providers.registry import register_provider
from providers.simple import DirectProvider

_ID_RE = re.compile(r"(\d{6}[_-]\d{2,4})")


def maker_movie_id(parsed: ParsedFilename, query: str = "") -> str:
    blob = " ".join(part for part in (parsed.code, parsed.original_name, query) if part)
    match = _ID_RE.search(blob)
    if not match:
        return ""
    return match.group(1).replace("-", "_")


def parse_maker_json(payload: dict, movie_id: str, provider_name: str, page_url: str = "") -> Optional[Metadata]:
    if not isinstance(payload, dict):
        return None
    title = payload.get("Title") or payload.get("TitleEn") or ""
    if not title:
        return None
    names = payload.get("ActressesJa") or []
    if not names and payload.get("Actor"):
        names = [part.strip() for part in str(payload["Actor"]).split(",") if part.strip()]
    actors = [Actor(name=str(name)) for name in names if name]
    runtime = payload.get("Duration")
    series = payload.get("Series") or ""
    return Metadata(
        title=str(title),
        code=str(payload.get("MovieID") or movie_id),
        year=year_of(str(payload.get("Release") or "")),
        actors=actors,
        genres=[str(series)] if series else [],
        runtime=int(runtime) if isinstance(runtime, (int, float)) else None,
        studio=str(payload.get("UCNAME") or "") or None,
        rating=float(payload["AvgRating"]) if isinstance(payload.get("AvgRating"), (int, float)) else None,
        poster_url=usable_poster_url(
            payload.get("ThumbUltra") or payload.get("ThumbHigh") or payload.get("MovieThumb") or ""
        ) or None,
        overview=str(payload.get("Desc") or "") or None,
        source_url=page_url,
        source_provider=provider_name,
    )


class _MakerJsonProvider(DirectProvider):
    api_template = ""

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(maker_movie_id(parsed))

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        movie_id = maker_movie_id(parsed, query)
        if not movie_id or not self.api_template:
            return None
        url = self.api_template.format(movie_id=movie_id)
        text, final = await fetch_text(self, url, headers={"Accept": "application/json"})
        if not text or not text.lstrip().startswith("{"):
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parse_maker_json(payload, movie_id, self.name, final)


@register_provider("tenmusume")
class TenMusumeProvider(_MakerJsonProvider):
    api_template = "https://www.10musume.com/dyn/phpauto/movie_details/movie_id/{movie_id}.json"

    @property
    def name(self) -> str:
        return "tenmusume"

    @property
    def display_name(self) -> str:
        return "10musume"


@register_provider("onepondo")
class OnePondoProvider(_MakerJsonProvider):
    api_template = "https://www.1pondo.tv/dyn/phpauto/movie_details/movie_id/{movie_id}.json"

    @property
    def name(self) -> str:
        return "onepondo"

    @property
    def display_name(self) -> str:
        return "1pondo"


@register_provider("pacopacomama")
class PacopacomamaProvider(_MakerJsonProvider):
    api_template = "https://www.pacopacomama.com/dyn/phpauto/movie_details/movie_id/{movie_id}.json"

    @property
    def name(self) -> str:
        return "pacopacomama"

    @property
    def display_name(self) -> str:
        return "Pacopacomama"
