"""直接按番号取回完整元数据的刮削源基类。"""

from __future__ import annotations

from typing import List, Optional

from core.models import Metadata, ParsedFilename, SearchResult
from providers.base import BaseProvider


class DirectProvider(BaseProvider):
    """search 阶段就返回完整 Metadata，避免再请求一次详情。"""

    @property
    def supported_types(self) -> List[str]:
        return ["coded"]

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        return None

    async def search(self, query: str, parsed: ParsedFilename) -> List[SearchResult]:
        metadata = await self.lookup(parsed, query)
        if metadata is None or not (metadata.title or metadata.code):
            return []
        metadata.source_provider = self.name
        return [
            SearchResult(
                provider=self.name,
                title=metadata.title or metadata.code or "",
                year=metadata.year,
                url=metadata.source_url,
                score=1.0,
                poster_url=metadata.poster_url,
                metadata=metadata,
            )
        ]

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        if result.metadata is not None:
            return result.metadata
        parsed = ParsedFilename(
            original_path=result.title or "",
            code=(result.extra or {}).get("code") or result.title,
        )
        return await self.lookup(parsed, parsed.code or result.title or "")
