"""HEYZO。只处理 HEYZO-编号，详情页为 /moviepages/{四位编号}/。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Actor, Metadata, ParsedFilename
from providers.page_parse import meta_content, year_of
from providers.registry import register_provider
from providers.simple import DirectProvider

_CODE_RE = re.compile(r"HEYZO-(\d{3,5})", re.I)


def heyzo_id(parsed: ParsedFilename, query: str = "") -> str:
    blob = " ".join(part for part in (parsed.code, parsed.original_name, query) if part)
    match = _CODE_RE.search(blob)
    return match.group(1).zfill(4) if match else ""


def parse_heyzo(html: str, movie_id: str, page_url: str = "") -> Optional[Metadata]:
    title = meta_content(html, "og:title")
    if not title:
        return None
    actor_row = re.search(
        r'<tr[^>]*class="[^"]*table-actor[^"]*"[^>]*>(.*?)</tr>',
        html,
        re.S | re.I,
    )
    actors = []
    if actor_row:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", actor_row.group(1), re.S | re.I)
        for cell in cells[1:] or cells:
            names = re.findall(r"<a[^>]*>([^<]+)</a>", cell, re.I)
            if not names:
                plain = re.sub(r"<[^>]+>", " ", cell)
                plain = re.sub(r"\s+", " ", plain).strip()
                if plain and plain not in {"出演", "演员"}:
                    names = [part.strip() for part in re.split(r"[,、/]", plain) if part.strip()]
            actors.extend(Actor(name=name.strip()) for name in names if name.strip())
    return Metadata(
        title=title,
        code=f"HEYZO-{movie_id}",
        year=year_of(html),
        actors=actors,
        poster_url=meta_content(html, "og:image") or None,
        source_url=page_url,
        source_provider="heyzo",
    )


@register_provider("heyzo")
class HeyzoProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "heyzo"

    @property
    def display_name(self) -> str:
        return "HEYZO"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(heyzo_id(parsed))

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        movie_id = heyzo_id(parsed, query)
        if not movie_id:
            return None
        url = f"https://www.heyzo.com/moviepages/{movie_id}/index.html"
        text, final = await fetch_text(self, url)
        if not text:
            return None
        return parse_heyzo(text, movie_id, final)
