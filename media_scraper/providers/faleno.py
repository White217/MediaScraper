"""Faleno。作品页为 /top/works/{番号小写}/。站点拒绝时返回无结果。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Metadata, ParsedFilename
from providers.page_parse import meta_content
from providers.registry import register_provider
from providers.simple import DirectProvider

_CODE_RE = re.compile(r"\b((?:FSDSS|FLNS|FCDSS|FLA|MAAN|MMND|FSDSS)-?\d{2,5})\b", re.I)


def faleno_slug(parsed: ParsedFilename, query: str = "") -> str:
    blob = " ".join(part for part in (parsed.code, parsed.original_name, query) if part)
    match = _CODE_RE.search(blob)
    if not match:
        return ""
    raw = match.group(1).upper()
    if "-" not in raw:
        raw = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", raw)
    return raw.lower()


def parse_faleno(html: str, code: str, page_url: str = "") -> Optional[Metadata]:
    title = meta_content(html, "og:title")
    if not title:
        return None
    return Metadata(
        title=title,
        code=code.upper(),
        poster_url=meta_content(html, "og:image") or None,
        source_url=page_url,
        source_provider="faleno",
    )


@register_provider("faleno")
class FalenoProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "faleno"

    @property
    def display_name(self) -> str:
        return "Faleno"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(faleno_slug(parsed))

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        slug = faleno_slug(parsed, query)
        if not slug:
            return None
        url = f"https://faleno.jp/top/works/{slug}/"
        text, final = await fetch_text(self, url, headers={"Referer": "https://faleno.jp/"})
        if not text:
            return None
        return parse_faleno(text, slug, final)
