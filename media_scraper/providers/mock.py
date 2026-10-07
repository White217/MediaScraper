"""离线测试用 Mock 刮削源。"""

from __future__ import annotations

from typing import List, Optional

from core.models import Actor, Metadata, ParsedFilename, SearchResult, VideoType
from providers.base import BaseProvider
from providers.registry import register_provider


@register_provider("mock")
class MockProvider(BaseProvider):
    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock Provider (测试)"

    @property
    def supported_types(self) -> List[str]:
        return ["movie", "episode", "coded", "unknown"]

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return parsed.video_type.value in self.supported_types

    def _build_query(self, parsed: ParsedFilename) -> str:
        if parsed.code:
            return parsed.code
        if parsed.title and parsed.year:
            return f"{parsed.title} {parsed.year}"
        return parsed.title or ""

    async def search(self, query: str, parsed: ParsedFilename) -> List[SearchResult]:
        if parsed.video_type == VideoType.CODED and parsed.code and not parsed.title:
            title = parsed.code
        elif parsed.title:
            title = parsed.title
        else:
            title = query or parsed.code or "Mock Title"
        url = f"https://mock.example.com/{parsed.video_type.value}/{title}"
        extra = {"code": parsed.code} if parsed.code else {}
        return [
            SearchResult(
                provider=self.name,
                title=title,
                year=parsed.year,
                url=url,
                score=1.0,
                extra=extra,
            )
        ]

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        title = result.title or "Mock Title"
        year = result.year
        code = (result.extra or {}).get("code")
        if not code and title and "-" in title and " " not in title:
            code = title
        rating = 8.8 if "Inception" in title else 7.0
        actors = [Actor(name="Mock Actor A"), Actor(name="Mock Actor B")]
        if title == "UnknownMovie":
            return Metadata(
                title=title,
                year=year or 2024,
                code=code,
                studio="Mock Studio",
                source_provider=self.name,
                source_url=result.url,
            )
        return Metadata(
            title=title,
            year=year,
            code=code,
            rating=rating,
            actors=actors,
            studio="Mock Studio",
            source_provider=self.name,
            source_url=result.url,
        )

    async def scrape(self, parsed: ParsedFilename) -> Optional[Metadata]:
        meta = await super().scrape(parsed)
        if meta is not None:
            meta.source_provider = self.name
        return meta
