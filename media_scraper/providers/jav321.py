"""Jav321。用番号 POST /search，跟随跳转到 /video/ 详情。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Actor, Metadata, ParsedFilename
from providers.page_parse import (
    anchor_names,
    bold_field,
    first_image,
    strip_tags,
    usable_poster_url,
    year_of,
)
from providers.registry import register_provider
from providers.simple import DirectProvider


def parse_jav321(html: str, page_url: str = "") -> Optional[Metadata]:
    if not html or "品番" not in html:
        return None
    code = strip_tags(bold_field(html, "品番"))
    if not code:
        return None
    released = strip_tags(bold_field(html, "配信開始日"))
    runtime_text = strip_tags(bold_field(html, "収録時間"))
    runtime_match = re.search(r"\d+", runtime_text)
    studio_html = bold_field(html, "メーカー")
    studios = anchor_names(studio_html)
    actors = [Actor(name=name) for name in anchor_names(bold_field(html, "出演者"))]
    genres = anchor_names(bold_field(html, "ジャンル"))
    heading = re.search(r'class="panel-heading"[^>]*>(.*?)</div>', html, re.S | re.I)
    title = strip_tags(heading.group(1)) if heading else ""
    if not title or title == code:
        title = code
    return Metadata(
        title=title,
        code=code.upper(),
        year=year_of(released),
        actors=actors,
        genres=genres,
        runtime=int(runtime_match.group(0)) if runtime_match else None,
        studio=studios[0] if studios else None,
        poster_url=usable_poster_url(first_image(html, "pics.dmm.co.jp", "pics.")) or None,
        source_url=page_url,
        source_provider="jav321",
    )


@register_provider("jav321")
class Jav321Provider(DirectProvider):
    @property
    def name(self) -> str:
        return "jav321"

    @property
    def display_name(self) -> str:
        return "Jav321"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(parsed.code) and super().can_handle(parsed)

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        text, final = await fetch_text(
            self,
            "https://www.jav321.com/search",
            data={"sn": parsed.code or query},
            use_cache=True,
        )
        if not text:
            return None
        return parse_jav321(text, final)
