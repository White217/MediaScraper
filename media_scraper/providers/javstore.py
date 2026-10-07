"""JavStore。搜索页里对上番号后再取标题。"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from core.models import Metadata, ParsedFilename
from providers.page_parse import meta_content, strip_tags
from providers.registry import register_provider
from providers.simple import DirectProvider


def parse_javstore(html: str, code: str, page_url: str = "") -> Optional[Metadata]:
    if not html or code.upper() not in html.upper():
        return None
    title = meta_content(html, "og:title")
    if not title:
        match = re.search(
            rf"<h[1-3][^>]*>[^<]*{re.escape(code)}[^<]*</h[1-3]>",
            html,
            re.I,
        )
        title = strip_tags(match.group(0)) if match else ""
    title = re.sub(rf"^{re.escape(code)}\s*", "", title, flags=re.I).strip()
    if not title:
        return None
    return Metadata(
        title=title,
        code=code.upper(),
        poster_url=meta_content(html, "og:image") or None,
        source_url=page_url,
        source_provider="javstore",
    )


@register_provider("javstore")
class JavStoreProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "javstore"

    @property
    def display_name(self) -> str:
        return "JavStore"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(parsed.code) and super().can_handle(parsed)

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        code = (parsed.code or query).upper()
        url = f"https://javstore.net/?s={quote(code)}"
        text, final = await fetch_text(self, url)
        if not text:
            return None
        return parse_javstore(text, code, final)
