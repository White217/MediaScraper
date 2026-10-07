"""Caribbeancom。只处理 6 位日期加 3 位序号，例如 010120-001。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Metadata, ParsedFilename
from providers.page_parse import absolute_url, first_image, tag_text
from providers.registry import register_provider
from providers.simple import DirectProvider

_CODE_RE = re.compile(r"(\d{6}-\d{3})")
_BASE = "https://www.caribbeancom.com"


def caribbean_id(parsed: ParsedFilename, query: str = "") -> str:
    blob = " ".join(part for part in (parsed.code, parsed.original_name, query) if part)
    match = _CODE_RE.search(blob)
    return match.group(1) if match else ""


def parse_caribbean(html: str, movie_id: str, page_url: str = "") -> Optional[Metadata]:
    if not html or "moviepages" not in html:
        return None
    title = tag_text(html, "h1") or movie_id
    if title.lower() in {"caribbeancom", "caribbean"}:
        return None
    poster = absolute_url(
        first_image(html, movie_id, "jacket", "poster"),
        page_url or _BASE,
    )
    return Metadata(
        title=title,
        code=movie_id,
        poster_url=poster or None,
        source_url=page_url,
        source_provider="caribbeancom",
    )


@register_provider("caribbeancom")
class CaribbeancomProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "caribbeancom"

    @property
    def display_name(self) -> str:
        return "Caribbeancom"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(caribbean_id(parsed))

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        movie_id = caribbean_id(parsed, query)
        if not movie_id:
            return None
        url = f"{_BASE}/moviepages/{movie_id}/index.html"
        text, final = await fetch_text(self, url)
        if not text:
            return None
        return parse_caribbean(text, movie_id, final)
