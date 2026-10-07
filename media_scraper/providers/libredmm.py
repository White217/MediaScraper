"""LibreDMM / LibreFanza。详情地址为 /movies/{番号}。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Actor, Metadata, ParsedFilename
from providers.page_parse import first_image, strip_tags, tag_text, usable_poster_url
from providers.registry import register_provider
from providers.simple import DirectProvider


def parse_libredmm(html: str, code: str, page_url: str = "") -> Optional[Metadata]:
    if not html or "<h1" not in html.lower():
        return None
    title = tag_text(html, "h1")
    if not title:
        return None
    actors = [
        Actor(name=strip_tags(name))
        for name in re.findall(r'href="/actresses/[^"]*"[^>]*>([^<]+)</a>', html, re.I)
        if strip_tags(name)
    ]
    return Metadata(
        title=title,
        code=code,
        actors=actors,
        poster_url=usable_poster_url(first_image(html, "pics.dmm.co.jp")) or None,
        source_url=page_url,
        source_provider="libredmm",
    )


@register_provider("libredmm")
class LibreDmmProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "libredmm"

    @property
    def display_name(self) -> str:
        return "LibreDMM"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(parsed.code) and super().can_handle(parsed)

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        code = (parsed.code or query).upper()
        url = f"https://www.libredmm.com/movies/{code}"
        text, final = await fetch_text(self, url)
        if not text:
            return None
        return parse_libredmm(text, code, final)
