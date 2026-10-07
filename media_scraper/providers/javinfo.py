"""
JavInfo Provider
通过 javinfo.dev 的聚合 API 刮削元数据。

- 官方 API: https://api.javinfo.dev
- 文档: https://javinfo.dev/llms.txt
- 认证: x-javinfo-key 请求头
- 端点: POST /movie（单个最佳匹配）、POST /query（列表）
- 该 API 内部按 fanza -> dmm -> javdatabase -> sextb -> javdb -> missav
  -> magneto -> javlibrary 的瀑布顺序聚合多个上游源

注意：javinfo 为付费 API（新注册有免费试用额度），
未配置 api_key 时本 Provider 自动禁用。
"""

import logging
from typing import List, Optional

from core.models import (
    Actor,
    Metadata,
    ParsedFilename,
    SearchResult,
)
from providers.base import BaseProvider
from providers.registry import register_provider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.javinfo.dev"


@register_provider("javinfo")
class JavInfoProvider(BaseProvider):
    """javinfo.dev 聚合 API Provider。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        proxy: Optional[str] = None,
        source_priority: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(proxy=proxy, connector=kwargs.get("connector"), **kwargs)
        self._api_key = (api_key or "").strip()
        self._proxy = proxy or None
        # 可选：指定上游源，如 "fanza,javdb"
        self._source_priority = source_priority or None

        if not self._api_key:
            logger.warning(
                "[javinfo] API Key 未配置，Provider 将无法工作。"
                "可在 https://app.javinfo.dev 注册获取。"
            )

    @property
    def name(self) -> str:
        return "javinfo"

    @property
    def display_name(self) -> str:
        return "JavInfo (聚合 API)"

    @property
    def supported_types(self) -> List[str]:
        return ["coded"]

    def can_handle(self, parsed: ParsedFilename) -> bool:
        # 无 key 直接拒绝，避免无意义请求
        if not self._api_key:
            return False
        return super().can_handle(parsed)

    async def _post(self, endpoint: str, payload: dict) -> Optional[dict]:
        """发起 POST 请求并返回 JSON。"""
        headers = {
            "x-javinfo-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        from providers.net import fetch_json

        data, _final = await fetch_json(
            self,
            f"{BASE_URL}{endpoint}",
            json_body=payload,
            headers=headers,
            use_cache=False,
        )
        return data if isinstance(data, dict) else None

    async def search(self, query: str, parsed: ParsedFilename) -> List[SearchResult]:
        # /query 用于列出候选
        payload = {"q": query}
        if self._source_priority:
            payload["providers"] = self._source_priority

        data = await self._post("/query", payload)
        if not data:
            return []

        results = []
        for item in data.get("results", []) or []:
            year = None
            rel = item.get("releaseDate") or ""
            if len(rel) >= 4 and rel[:4].isdigit():
                year = int(rel[:4])
            results.append(
                SearchResult(
                    provider=self.name,
                    title=item.get("title") or item.get("dvdId") or query,
                    year=year,
                    url=(item.get("extra") or {}).get("pageUrl"),
                    poster_url=item.get("cover"),
                    extra={"dvdId": item.get("dvdId"), "id": item.get("id")},
                )
            )
        return results

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        # /movie 返回完整元数据；用 dvdId 或原 query 再查一次
        q = result.extra.get("dvdId") or result.title
        payload = {"q": q}
        if self._source_priority:
            payload["providers"] = self._source_priority

        data = await self._post("/movie", payload)
        if not data:
            return None

        res = data.get("result")
        if not res:
            return None

        return self._map_metadata(res, data.get("source"))

    def _map_metadata(self, res: dict, upstream: Optional[str]) -> Optional[Metadata]:
        """把统一响应映射为内部 Metadata。"""
        # 标题优先英文（对国内刮削更通用），否则日文
        title = res.get("titleEn") or res.get("titleJa") or res.get("dvdId")
        if not title:
            return None

        year = None
        rel = res.get("releaseDate") or ""
        if len(rel) >= 4 and rel[:4].isdigit():
            year = int(rel[:4])

        actors = [
            Actor(name=n) for n in (res.get("actresses") or []) if n
        ]

        extra = dict(res.get("extra") or {})
        extra["upstream_source"] = upstream

        meta = Metadata(
            title=title,
            original_title=res.get("titleJa"),
            year=year,
            code=res.get("dvdId"),
            actors=actors,
            genres=list(res.get("categories") or []),
            overview=res.get("commentEn") or res.get("commentJa"),
            runtime=res.get("runtimeMins"),
            studio=(res.get("makers") or [None])[0],
            poster_url=res.get("jacketFullUrl") or res.get("jacketThumbUrl"),
            extra=extra,
        )
        return meta

    async def scrape(self, parsed: ParsedFilename) -> Optional[Metadata]:
        """直接走 /movie，一次调用拿最佳匹配。"""
        if not self.can_handle(parsed):
            return None

        query = self._build_query(parsed)
        if not query:
            return None

        payload = {"q": query}
        if self._source_priority:
            payload["providers"] = self._source_priority

        logger.info(f"[javinfo] /movie 查询: {query}")
        data = await self._post("/movie", payload)
        if not data or not data.get("result"):
            return None

        meta = self._map_metadata(data["result"], data.get("source"))
        if meta:
            meta.source_provider = self.name
            meta.source_url = BASE_URL
        return meta
