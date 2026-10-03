"""JavMenu。中文详情页为 /zh/{番号}，标题和封面走 Open Graph。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Metadata, ParsedFilename
from providers.page_parse import meta_content, tag_text, year_of
from providers.registry import register_provider
from providers.simple import DirectProvider

_BASE = "https://javmenu.com"


def parse_javmenu(html: str, code: str, page_url: str = "") -> Optional[Metadata]:
    title = meta_content(html, "og:title") or tag_text(html, "h1")
    if not title:
        return None
    title = re.sub(rf"^{re.escape(code)}\s*", "", title, flags=re.I).strip() or title
    poster = meta_content(html, "og:image")
    return Metadata(
        title=title,
        code=code,
        year=year_of(title),
        poster_url=poster or None,
        overview=meta_content(html, "og:description") or None,
        source_url=page_url,
        source_provider="javmenu",
    )


@register_provider("javmenu")
class JavMenuProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "javmenu"

    @property
    def display_name(self) -> str:
        return "JavMenu"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(parsed.code) and super().can_handle(parsed)

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        code = (parsed.code or query).upper()
        url = f"{_BASE}/zh/{code}"
        text, final = await fetch_text(self, url)
        if not text or "og:title" not in text:
            return None
        return parse_javmenu(text, code, final)
