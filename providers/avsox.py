"""AVSOX。页面结构和 JavBus 同一类；站点若只返回空壳页则视为无结果。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Actor, Metadata, ParsedFilename
from providers.page_parse import first_image, strip_tags, tag_text, usable_poster_url, year_of
from providers.registry import register_provider
from providers.simple import DirectProvider

_BASE = "https://avsox.click"


def _avsox_poster(html: str) -> str:
    """bigImage 的 href 是大图。页面里没有时再退回 img。"""
    patterns = (
        r'<a\b[^>]*class="[^"]*\bbigImage\b[^"]*"[^>]*href="([^"]+)"',
        r'<a\b[^>]*href="([^"]+)"[^>]*class="[^"]*\bbigImage\b[^"]*"',
    )
    for pattern in patterns:
        match = re.search(pattern, html or "", re.I)
        if match and match.group(1).strip():
            return usable_poster_url(match.group(1))
    return usable_poster_url(first_image(html, "cover", "big"))


def parse_avsox(html: str, code: str, page_url: str = "") -> Optional[Metadata]:
    if not html or ("bigImage" not in html and "movie-box" not in html and "識別碼" not in html):
        return None
    if "bigImage" not in html and "識別碼" not in html:
        link = re.search(r'class="movie-box"\s+href="([^"]+)"', html, re.I)
        if not link:
            return None
        return Metadata(title=code, code=code, source_url=link.group(1), source_provider="avsox")
    title = tag_text(html, "h3") or code
    info = re.search(r'識別碼:\s*</span>\s*([^<]+)', html)
    found_code = strip_tags(info.group(1)) if info else code
    released = re.search(r'發行日期:\s*</span>\s*([^<]+)', html)
    actors = [
        Actor(name=strip_tags(name))
        for name in re.findall(r'class="star-name"[^>]*>.*?<a[^>]*>([^<]+)</a>', html, re.S)
    ]
    if not actors:
        genre_block = re.search(r"演員([\s\S]{0,800})", html)
        if genre_block:
            actors = [Actor(name=name) for name in re.findall(r"<a[^>]*>([^<]+)</a>", genre_block.group(1))]
    return Metadata(
        title=title,
        code=(found_code or code).upper(),
        year=year_of(released.group(1) if released else ""),
        actors=actors,
        poster_url=_avsox_poster(html) or None,
        source_url=page_url,
        source_provider="avsox",
    )


@register_provider("avsox")
class AvsoxProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "avsox"

    @property
    def display_name(self) -> str:
        return "AVSOX"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(parsed.code) and super().can_handle(parsed)

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        code = (parsed.code or query).upper()
        direct = f"{_BASE}/cn/{code}"
        text, final = await fetch_text(self, direct, use_cache=False)
        metadata = parse_avsox(text or "", code, final)
        if metadata and metadata.title != code:
            return metadata
        if metadata and metadata.poster_url:
            return metadata
        search_url = f"{_BASE}/cn/search/{code}"
        text, final = await fetch_text(self, search_url, use_cache=False)
        if not text:
            return metadata
        found = parse_avsox(text, code, final)
        if found and found.source_url and "search" not in found.source_url and "bigImage" not in text:
            detail, detail_url = await fetch_text(self, found.source_url, use_cache=False)
            parsed_detail = parse_avsox(detail or "", code, detail_url)
            return parsed_detail or found
        return found or metadata
