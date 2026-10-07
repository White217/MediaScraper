"""AV Entertainment。旧搜索地址会返回 410，没有商品页时不编造结果。"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from core.models import Metadata, ParsedFilename
from providers.page_parse import meta_content, strip_tags
from providers.registry import register_provider
from providers.simple import DirectProvider

_SEARCH = "https://www.aventertainments.com/search_Products.aspx?keyword={keyword}"


def parse_aventertainments(html: str, code: str, page_url: str = "") -> Optional[Metadata]:
    if not html or code.upper() not in html.upper():
        return None
    if not re.search(r"product", html, re.I):
        return None
    title = meta_content(html, "og:title")
    if not title:
        match = re.search(rf"<a[^>]+product[^>]*>([^<]*{re.escape(code)}[^<]*)</a>", html, re.I)
        title = strip_tags(match.group(1)) if match else ""
    if not title:
        return None
    return Metadata(
        title=title,
        code=code.upper(),
        poster_url=meta_content(html, "og:image") or None,
        source_url=page_url,
        source_provider="aventertainments",
    )


@register_provider("aventertainments")
class AvEntertainmentsProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "aventertainments"

    @property
    def display_name(self) -> str:
        return "AV Entertainment"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(parsed.code) and super().can_handle(parsed)

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        code = (parsed.code or query).upper()
        url = _SEARCH.format(keyword=quote(code))
        text, final = await fetch_text(self, url)
        if not text:
            return None
        return parse_aventertainments(text, code, final)
