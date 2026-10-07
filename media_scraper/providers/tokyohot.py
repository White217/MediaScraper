"""Tokyo-Hot。只处理 n 加数字的品番，例如 n1501。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Metadata, ParsedFilename
from providers.page_parse import meta_content
from providers.registry import register_provider
from providers.simple import DirectProvider

_CODE_RE = re.compile(r"\b(n\d{3,5})\b", re.I)


def tokyo_id(parsed: ParsedFilename, query: str = "") -> str:
    blob = " ".join(part for part in (parsed.code, parsed.original_name, query) if part)
    match = _CODE_RE.search(blob)
    return match.group(1).lower() if match else ""


def parse_tokyohot(html: str, movie_id: str, page_url: str = "") -> Optional[Metadata]:
    title = meta_content(html, "og:title")
    if not title:
        return None
    return Metadata(
        title=title,
        code=movie_id,
        poster_url=meta_content(html, "og:image") or None,
        source_url=page_url,
        source_provider="tokyohot",
    )


@register_provider("tokyohot")
class TokyoHotProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "tokyohot"

    @property
    def display_name(self) -> str:
        return "Tokyo-Hot"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(tokyo_id(parsed))

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        movie_id = tokyo_id(parsed, query)
        if not movie_id:
            return None
        url = f"https://my.tokyo-hot.com/product/{movie_id}/"
        text, final = await fetch_text(self, url)
        if not text:
            return None
        return parse_tokyohot(text, movie_id, final)
