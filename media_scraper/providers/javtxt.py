"""JavTXT 档案馆。搜索页用 work-id 卡片对上番号。"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from core.models import Actor, Metadata, ParsedFilename
from providers.page_parse import strip_tags
from providers.registry import register_provider
from providers.simple import DirectProvider


def parse_javtxt(html: str, code: str, page_url: str = "") -> Optional[Metadata]:
    if not html or "work-id" not in html:
        return None
    for match in re.finditer(r'class="work-id"[^>]*>([^<]+)', html, re.I):
        found = strip_tags(match.group(1)).upper()
        if found != code.upper():
            continue
        window = html[match.start(): match.start() + 600]
        actress = re.search(r'class="work-actress"[^>]*>([^<]+)', window, re.I)
        actors = []
        if actress and strip_tags(actress.group(1)):
            actors.append(Actor(name=strip_tags(actress.group(1))))
        return Metadata(
            title=found,
            code=found,
            actors=actors,
            source_url=page_url,
            source_provider="javtxt",
        )
    return None


@register_provider("javtxt")
class JavTxtProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "javtxt"

    @property
    def display_name(self) -> str:
        return "JavTXT"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(parsed.code) and super().can_handle(parsed)

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        code = (parsed.code or query).upper()
        url = f"https://javtxt.com/?s={quote(code)}"
        text, final = await fetch_text(self, url)
        if not text:
            return None
        return parse_javtxt(text, code, final)
